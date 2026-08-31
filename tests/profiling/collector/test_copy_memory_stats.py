import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_copy_memory_error_count",
        DD_PROFILING_UPLOAD_INTERVAL="1",
    ),
    err=None,
)
def test_copy_memory_error_count_present():
    """copy_memory_error_count is always emitted (even when 0) and is non-negative."""
    import json
    import os
    import time

    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    p = profiler.Profiler(tracer=tracer)
    p.start()
    time.sleep(3)
    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    files = pprof_utils.get_internal_metadata_files(output_filename)
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
    import json
    import os
    import time

    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    p = profiler.Profiler(tracer=tracer)
    p.start()
    time.sleep(3)
    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    files = pprof_utils.get_internal_metadata_files(output_filename)
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


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_fast_copy_memory_enabled",
        DD_PROFILING_UPLOAD_INTERVAL="1",
        _DD_PROFILING_STACK_FAST_COPY="1",
    ),
    err=None,
)
def test_fast_copy_memory_enabled() -> None:
    """Sampler runs on the syscall copy during warmup, then upgrades to safe_memcpy (PROF-15342)."""
    import json
    import os
    import time
    from typing import Any
    from typing import Optional

    # Underscore-prefixed, so only on the _stack submodule (`import *` skips it).
    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    _stack._set_fast_copy_warmup_seconds(1.0)

    p: profiler.Profiler = profiler.Profiler(tracer=tracer)
    p.start()

    # Require warmup (False) before accepting the upgrade (True), so the brief
    # constructor-time True isn't mistaken for it.
    saw_warmup: bool = False
    saw_upgrade: bool = False
    deadline: float = time.monotonic() + 10
    while time.monotonic() < deadline:
        active: bool = _stack.fast_copy_memory_active()
        if not saw_warmup:
            if active is False:
                saw_warmup = True
        elif active is True:
            saw_upgrade = True
            break
        time.sleep(0.05)

    assert saw_warmup, "Expected the sampler to run on the syscall copy during the warmup window"
    assert saw_upgrade, "Expected the sampler to upgrade to safe_memcpy after warmup"

    # The upgrade flips the flag at the start of a sampling cycle, but the stats it
    # feeds are only written at the end of that cycle, so stopping here could flush a
    # window that never saw the upgrade. Wait out an upload interval instead.
    time.sleep(2)
    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    files = pprof_utils.get_internal_metadata_files(output_filename)
    assert files, "Expected at least one internal_metadata.json file"

    # A window with no completed sampling cycle inherits the previous window's fast-copy
    # state, so it says nothing about what the sampler is running on.
    metadata: Optional[dict[str, Any]] = None
    for f in reversed(files):
        with open(f) as fp:
            candidate = json.load(fp)

        if candidate["sampling_event_count"] > 0:
            metadata = candidate
            break

    assert metadata is not None, f"Expected an upload window with at least one sampling cycle: {files}"

    assert metadata["fast_copy_memory_user_disabled"] is False, metadata
    assert metadata["fast_copy_memory_capable"] is True, metadata
    assert metadata["fast_copy_memory_syscall_fallback"] is False, metadata
    assert metadata["fast_copy_memory_enabled"] is True, metadata
    assert metadata["fast_copy_memory_desired"] is True, metadata
    assert metadata["fast_copy_memory_foreign_takeover"] is False, metadata


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_fast_copy_faulthandler_warmup",
        DD_PROFILING_UPLOAD_INTERVAL="1",
        _DD_PROFILING_STACK_FAST_COPY="1",
    ),
    err=None,
)
def test_fast_copy_faulthandler_enable_during_warmup() -> None:
    """faulthandler.enable() inside the warmup window must not cost us the handler (PROF-15342)."""
    import faulthandler

    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_utils import wait_for_fast_copy_state

    _stack._set_fast_copy_warmup_seconds(3.0)

    p: profiler.Profiler = profiler.Profiler(tracer=tracer)
    p.start()

    # Land inside the warmup window: fast copy is inactive here, but our SIGSEGV/SIGBUS
    # handlers are still installed, since warmup only swaps the copy function.
    assert wait_for_fast_copy_state(_stack, False), "sampler never dropped to the syscall copy"

    # ddtrace wraps faulthandler.enable to pause sampling, step out of the handler chain
    # and reclaim on top. Those hooks used to be gated on the transient fast-copy flag, so
    # during warmup both were no-ops and faulthandler kept ownership for good.
    faulthandler.enable()
    assert _stack.segv_handler_installed(), "handler not reclaimed after faulthandler.enable()"

    upgraded: bool = wait_for_fast_copy_state(_stack, True, timeout=20.0)
    p.stop()

    assert upgraded, "faulthandler.enable() during warmup pinned the process to the syscall copy"


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_fast_copy_fork_during_warmup",
        DD_PROFILING_UPLOAD_INTERVAL="1",
        _DD_PROFILING_STACK_FAST_COPY="1",
    ),
    err=None,
)
def test_fast_copy_fork_during_warmup() -> None:
    """A child forked mid-warmup re-runs the warmup decision rather than inheriting it (PROF-15342)."""
    import os

    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_utils import wait_for_fast_copy_state

    _stack._set_fast_copy_warmup_seconds(3.0)

    p: profiler.Profiler = profiler.Profiler(tracer=tracer)
    p.start()

    # Fork while the sampler is still warming up on the syscall copy. This is the common
    # shape for gunicorn and celery prefork, which fork their workers moments after start.
    assert wait_for_fast_copy_state(_stack, False), "sampler never dropped to the syscall copy"

    pid: int = os.fork()
    if pid == 0:
        # The atfork hook restarts the sampler here. Deriving the fast-copy intent from
        # the transient flag left the child on the syscall copy for its whole life.
        try:
            child_upgraded = wait_for_fast_copy_state(_stack, True, timeout=20.0)
        except BaseException:
            os._exit(2)
        os._exit(0 if child_upgraded else 1)

    _, status = os.waitpid(pid, 0)
    p.stop()

    assert os.WIFEXITED(status), f"child did not exit normally: {status}"
    assert os.WEXITSTATUS(status) == 0, "child forked mid-warmup never upgraded to safe_memcpy"
