import sys

import pytest

from ddtrace.internal.datadog.profiling import stack as _stack_ext


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_copy_memory_error_count",
        DD_PROFILING_UPLOAD_INTERVAL="1",
    ),
    err=None,
)
def test_copy_memory_error_count_present():
    """copy_memory_error_count is always emitted (even when 0) and is non-negative."""
    import glob
    import json
    import os
    import time

    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer

    p = profiler.Profiler(tracer=tracer)
    p.start()
    time.sleep(3)
    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    files = sorted(glob.glob(output_filename + ".*.internal_metadata.json"))
    assert files, "Expected at least one internal_metadata.json file"

    for f in files:
        with open(f) as fp:
            metadata = json.load(fp)
        assert "copy_memory_error_count" in metadata, f"Missing copy_memory_error_count in {f}: {metadata}"
        assert metadata["copy_memory_error_count"] >= 0, f"copy_memory_error_count must be non-negative: {metadata}"
        assert "fast_copy_memory_user_disabled" in metadata, (
            f"Missing fast_copy_memory_user_disabled in {f}: {metadata}"
        )
        assert "fast_copy_memory_capable" in metadata, f"Missing fast_copy_memory_capable in {f}: {metadata}"
        assert "fast_copy_memory_syscall_fallback" in metadata, (
            f"Missing fast_copy_memory_syscall_fallback in {f}: {metadata}"
        )
        assert "fast_copy_memory_desired" in metadata, f"Missing fast_copy_memory_desired in {f}: {metadata}"
        assert "fast_copy_memory_foreign_takeover" in metadata, (
            f"Missing fast_copy_memory_foreign_takeover in {f}: {metadata}"
        )


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_fast_copy_memory_disabled",
        DD_PROFILING_UPLOAD_INTERVAL="1",
        _DD_PROFILING_STACK_FAST_COPY="false",
    ),
    err=None,
)
def test_fast_copy_memory_disabled():
    """fast_copy_memory_enabled is False when _DD_PROFILING_STACK_FAST_COPY=false."""
    import glob
    import json
    import os
    import time

    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer

    p = profiler.Profiler(tracer=tracer)
    p.start()
    time.sleep(3)
    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    files = sorted(glob.glob(output_filename + ".*.internal_metadata.json"))
    assert files, "Expected at least one internal_metadata.json file"

    for i, f in enumerate(files):
        is_last_file = i == len(files) - 1
        with open(f) as fp:
            metadata = json.load(fp)
        if not is_last_file:
            assert "fast_copy_memory_enabled" in metadata, f"Missing fast_copy_memory_enabled in {f}: {metadata}"
            assert metadata["fast_copy_memory_enabled"] is False, (
                f"Expected fast_copy_memory_enabled=false when _DD_PROFILING_STACK_FAST_COPY=false: {metadata}"
            )
            assert metadata["fast_copy_memory_user_disabled"] is True, metadata
            assert metadata["fast_copy_memory_syscall_fallback"] is False, metadata
            assert metadata["fast_copy_memory_desired"] is False, metadata
            assert metadata["fast_copy_memory_foreign_takeover"] is False, metadata


@pytest.mark.skipif(sys.platform == "win32", reason="stack v2 profiler is not available on Windows")
@pytest.mark.skipif(not _stack_ext.is_available, reason="stack v2 native extension not available")
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_fast_copy_memory_enabled",
        DD_PROFILING_UPLOAD_INTERVAL="1",
        _DD_PROFILING_STACK_FAST_COPY="1",
    ),
    err=None,
)
def test_fast_copy_memory_enabled() -> None:
    """Warmup on syscall copy, then upgrade to safe_memcpy."""
    import glob
    import json
    import os
    import time

    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_utils import wait_for_fast_copy_upgrade

    _stack._set_fast_copy_warmup_seconds(1.0)

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())

    p: profiler.Profiler = profiler.Profiler(tracer=tracer)
    p.start()

    saw_warmup, saw_upgrade = wait_for_fast_copy_upgrade(_stack, require_warmup=False)

    # Wait for a metadata upload cycle while the profiler is still running so
    # fast_copy_memory_enabled reflects the post-warmup state before stop().
    metadata = None
    if saw_upgrade:
        meta_deadline = time.monotonic() + 5
        while time.monotonic() < meta_deadline:
            files = sorted(glob.glob(output_filename + ".*.internal_metadata.json"))
            if files:
                with open(files[-1]) as fp:
                    candidate = json.load(fp)
                if candidate.get("fast_copy_memory_enabled") is True:
                    metadata = candidate
                    break
            time.sleep(0.1)

    p.stop()

    if metadata is None:
        files = sorted(glob.glob(output_filename + ".*.internal_metadata.json"))
        assert files, "Expected at least one internal_metadata.json file"
        with open(files[-1]) as fp:
            metadata = json.load(fp)

    assert metadata["fast_copy_memory_user_disabled"] is False, metadata
    assert metadata["fast_copy_memory_capable"] is True, metadata
    assert metadata["fast_copy_memory_syscall_fallback"] is False, metadata
    assert metadata["fast_copy_memory_enabled"] is True, metadata
    assert metadata["fast_copy_memory_desired"] is True, metadata
    assert metadata["fast_copy_memory_foreign_takeover"] is False, metadata

    if saw_warmup:
        assert saw_upgrade, "Expected the sampler to upgrade to safe_memcpy after warmup"
    else:
        assert saw_upgrade, "Expected safe_memcpy to stay active when warmup is skipped"


@pytest.mark.skipif(sys.platform == "win32", reason="stack v2 profiler is not available on Windows")
@pytest.mark.skipif(not _stack_ext.is_available, reason="stack v2 native extension not available")
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_fast_copy_reinstall_during_warmup",
        DD_PROFILING_UPLOAD_INTERVAL="1",
        _DD_PROFILING_STACK_FAST_COPY="1",
    ),
    out="OK\n",
    err=None,
)
def test_fast_copy_reinstall_segv_handler_works_during_warmup() -> None:
    """Reinstall reclaims handler during warmup (keys off fast_copy_desired)."""
    import signal
    import time

    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer

    _stack._set_fast_copy_warmup_seconds(30.0)

    p: profiler.Profiler = profiler.Profiler(tracer=tracer)
    p.start()
    try:
        deadline: float = time.monotonic() + 5
        while time.monotonic() < deadline and _stack.fast_copy_memory_active() is not False:
            time.sleep(0.02)
        assert _stack.fast_copy_memory_active() is False, "expected the syscall copy during warmup"
        assert _stack.segv_handler_installed() is True

        signal.signal(signal.SIGSEGV, signal.SIG_DFL)
        assert _stack.segv_handler_installed() is False
        _stack.reinstall_segv_handler()
        assert _stack.segv_handler_installed() is True, "reinstall must reclaim the handler during warmup"
    finally:
        p.stop()

    print("OK")


@pytest.mark.skipif(sys.platform == "win32", reason="stack v2 profiler is not available on Windows")
@pytest.mark.skipif(not _stack_ext.is_available, reason="stack v2 native extension not available")
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_fast_copy_takeover_fallback",
        DD_PROFILING_UPLOAD_INTERVAL="1",
        _DD_PROFILING_STACK_FAST_COPY="1",
    ),
    out="OK\n",
    err=None,
)
def test_fast_copy_falls_back_and_does_not_reclaim_after_takeover() -> None:
    """Foreign takeover forces permanent syscall-copy fallback."""
    import glob
    import json
    import os
    import signal
    import time

    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_utils import wait_for_fast_copy_upgrade

    _stack._set_fast_copy_warmup_seconds(0.5)

    p: profiler.Profiler = profiler.Profiler(tracer=tracer)
    p.start()
    try:
        wait_for_fast_copy_upgrade(_stack)
        assert _stack.segv_handler_installed() is True

        signal.signal(signal.SIGSEGV, signal.SIG_DFL)
        assert _stack.segv_handler_installed() is False

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and _stack.fast_copy_memory_active() is not False:
            time.sleep(0.02)
        assert _stack.fast_copy_memory_active() is False, "sampler did not fall back after the takeover"

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if _stack.fast_copy_memory_active() is False:
                _stack.reinstall_segv_handler()
                if _stack.segv_handler_installed() is False:
                    break
            time.sleep(0.05)
        else:
            assert False, "handler must not be reclaimed after a foreign takeover"

        time.sleep(0.5)
        assert _stack.fast_copy_memory_active() is False, "fast copy must stay disabled after a foreign takeover"
    finally:
        p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    files = sorted(glob.glob(output_filename + ".*.internal_metadata.json"))
    assert files, "Expected at least one internal_metadata.json file"
    with open(files[-1]) as fp:
        metadata = json.load(fp)
    assert metadata["fast_copy_memory_desired"] is True, metadata
    assert metadata["fast_copy_memory_enabled"] is False, metadata
    assert metadata["fast_copy_memory_syscall_fallback"] is True, metadata
    assert metadata["fast_copy_memory_foreign_takeover"] is True, metadata

    print("OK")


@pytest.mark.skipif(sys.platform == "win32", reason="fork not supported on Windows")
@pytest.mark.skipif(not _stack_ext.is_available, reason="stack v2 native extension not available")
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_fast_copy_fork_rewarm",
        DD_PROFILING_UPLOAD_INTERVAL="1",
        _DD_PROFILING_STACK_FAST_COPY="1",
    ),
    err=None,
)
def test_fast_copy_rewarms_in_forked_child_during_warmup() -> None:
    """Child forked during warmup re-warms into safe_memcpy."""
    import os
    import time

    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer

    _stack._set_fast_copy_warmup_seconds(10.0)

    p: profiler.Profiler = profiler.Profiler(tracer=tracer)
    p.start()
    try:
        deadline: float = time.monotonic() + 5
        while time.monotonic() < deadline and _stack.fast_copy_memory_active() is not False:
            time.sleep(0.02)
        assert _stack.fast_copy_memory_active() is False, "expected syscall copy during warmup before fork"

        pid: int = os.fork()
        if pid == 0:
            try:
                saw_upgrade: bool = False
                child_deadline: float = time.monotonic() + 15
                while time.monotonic() < child_deadline:
                    if _stack.fast_copy_memory_active() is True:
                        saw_upgrade = True
                        break
                    time.sleep(0.05)
                if not saw_upgrade:
                    os._exit(1)
            except Exception:
                os._exit(1)
            os._exit(0)
        else:
            _, status = os.waitpid(pid, 0)
            assert os.WIFEXITED(status), f"child killed by signal {os.WTERMSIG(status)}"
            assert os.WEXITSTATUS(status) == 0, "child did not re-warm into fast copy after fork"
    finally:
        p.stop()


@pytest.mark.skipif(sys.platform == "win32", reason="fork not supported on Windows")
@pytest.mark.skipif(not _stack_ext.is_available, reason="stack v2 native extension not available")
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_fast_copy_fork_takeover",
        DD_PROFILING_UPLOAD_INTERVAL="1",
        _DD_PROFILING_STACK_FAST_COPY="1",
    ),
    err=None,
)
def test_forked_child_inherits_foreign_takeover_after_parent_fallback() -> None:
    """A child inherits the sticky foreign-takeover flag and stays on the syscall copy."""
    import os
    import signal
    import time

    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_utils import wait_for_fast_copy_upgrade

    _stack._set_fast_copy_warmup_seconds(0.5)

    p: profiler.Profiler = profiler.Profiler(tracer=tracer)
    p.start()
    try:
        wait_for_fast_copy_upgrade(_stack)

        signal.signal(signal.SIGSEGV, signal.SIG_DFL)
        assert _stack.segv_handler_installed() is False

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and _stack.fast_copy_memory_active() is not False:
            time.sleep(0.02)
        assert _stack.fast_copy_memory_active() is False, "parent did not fall back after takeover"

        pid: int = os.fork()
        if pid == 0:
            try:
                _stack.reinstall_segv_handler()
                if _stack.segv_handler_installed() is not False:
                    os._exit(1)
                if _stack.fast_copy_memory_active() is not False:
                    os._exit(2)
            except Exception:
                os._exit(3)
            os._exit(0)
        else:
            _, status = os.waitpid(pid, 0)
            assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, (
                f"child did not inherit sticky foreign takeover (status={status})"
            )
    finally:
        p.stop()


@pytest.mark.skipif(sys.platform == "win32", reason="fork not supported on Windows")
@pytest.mark.skipif(not _stack_ext.is_available, reason="stack v2 native extension not available")
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_fork_pauses_sampler",
        DD_PROFILING_UPLOAD_INTERVAL="1",
        _DD_PROFILING_STACK_FAST_COPY="1",
    ),
    err=None,
)
def test_fork_pauses_sampler_before_safe_memcpy() -> None:
    """prefork() pauses the sampler so fork() does not span safe_memcpy."""
    import os

    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_utils import wait_for_fast_copy_upgrade

    _stack._set_fast_copy_warmup_seconds(0.5)

    p: profiler.Profiler = profiler.Profiler(tracer=tracer)
    p.start()
    try:
        wait_for_fast_copy_upgrade(_stack)

        pid: int = os.fork()
        if pid == 0:
            os._exit(0)
        else:
            _, status = os.waitpid(pid, 0)
            assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, f"child failed (status={status})"
            assert _stack._take_prefork_pause_observation() is True, "prefork did not pause the sampler"
            assert _stack._sampling_paused() is False, "parent sampler must resume after fork"
    finally:
        p.stop()
