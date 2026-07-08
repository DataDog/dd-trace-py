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


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_fast_copy_memory_enabled",
        DD_PROFILING_UPLOAD_INTERVAL="1",
        _DD_PROFILING_STACK_FAST_COPY="1",
    ),
    err=None,
)
def test_fast_copy_memory_enabled() -> None:
    """Sampler runs on the syscall copy during warmup, then upgrades to safe_memcpy (PROF-14568)."""
    import glob
    import json
    import os
    import time

    # Underscore-prefixed, so only on the _stack submodule (`import *` skips it).
    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer

    _stack._set_fast_copy_warmup_seconds(1.0)

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())

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

    # When process_vm_readv is unavailable the sampler skips warmup and stays on safe_memcpy.
    if not saw_upgrade and _stack.fast_copy_memory_active():
        saw_upgrade = True

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

    if saw_warmup:
        assert saw_upgrade, "Expected the sampler to upgrade to safe_memcpy after warmup"
    else:
        assert saw_upgrade, "Expected safe_memcpy to stay active when warmup is skipped"


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
    """Handler reclaim keys off the persistent fast_copy_desired intent (PROF-14568).

    During the startup warmup the sampler runs on the syscall copy, so fast_copy_active
    is False, but the (un)install gate uses the persistent fast_copy_desired instead.
    A handler swap coordinated during that window must therefore still be reclaimed
    rather than leaving a foreign handler in place and forcing a permanent fallback.
    """
    import signal
    import time

    # Underscore-prefixed, so only on the _stack submodule (`import *` skips it).
    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer

    # Long warmup so we can act while the sampler is still on the syscall copy.
    _stack._set_fast_copy_warmup_seconds(30.0)

    p: profiler.Profiler = profiler.Profiler(tracer=tracer)
    p.start()
    try:
        # Wait for the sampler thread to start and drop to the syscall copy. The brief
        # constructor-time active state is cleared once warmup begins.
        deadline: float = time.monotonic() + 5
        while time.monotonic() < deadline and _stack.fast_copy_memory_active() is not False:
            time.sleep(0.02)
        assert _stack.fast_copy_memory_active() is False, "expected the syscall copy during warmup"
        assert _stack.segv_handler_installed() is True

        # A component overwrites SIGSEGV mid-warmup. Reclaim must work even though fast
        # copy is inactive right now, because the gate is on fast_copy_desired.
        signal.signal(signal.SIGSEGV, signal.SIG_DFL)
        assert _stack.segv_handler_installed() is False
        _stack.reinstall_segv_handler()
        assert _stack.segv_handler_installed() is True, "reinstall must reclaim the handler during warmup"
    finally:
        p.stop()

    print("OK")


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
    """A mid-run foreign takeover forces a permanent, non-reclaiming fallback (PROF-14568).

    Once the sampler has upgraded to safe_memcpy, a foreign SIGSEGV/SIGBUS handler
    installed while sampling is running must be detected (before the interp/thread-list
    traversal, so the process does not crash) and the sampler must fall back to the
    syscall copy. From then on the fallback is sticky: reclaim is a no-op so a later
    cooperative swap cannot hand ownership back to us on top of the foreign handler.
    """
    import signal
    import time

    # Underscore-prefixed, so only on the _stack submodule (`import *` skips it).
    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer

    # Short warmup so the sampler upgrades to safe_memcpy quickly.
    _stack._set_fast_copy_warmup_seconds(0.5)

    p: profiler.Profiler = profiler.Profiler(tracer=tracer)
    p.start()
    try:
        # Wait for the upgrade to safe_memcpy.
        deadline: float = time.monotonic() + 10
        while time.monotonic() < deadline and _stack.fast_copy_memory_active() is not True:
            time.sleep(0.02)
        assert _stack.fast_copy_memory_active() is True, "sampler never upgraded to safe_memcpy"
        assert _stack.segv_handler_installed() is True

        # A foreign component takes over SIGSEGV while the sampler is running. The next
        # cycle must detect the loss of ownership and fall back to the syscall copy.
        signal.signal(signal.SIGSEGV, signal.SIG_DFL)
        assert _stack.segv_handler_installed() is False

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and _stack.fast_copy_memory_active() is not False:
            time.sleep(0.02)
        assert _stack.fast_copy_memory_active() is False, "sampler did not fall back after the takeover"

        # Sticky fallback: reclaim is now a no-op, so we never reinstall our handler on
        # top of the foreign owner that caused the fallback.
        _stack.reinstall_segv_handler()
        assert _stack.segv_handler_installed() is False, "handler must not be reclaimed after a foreign takeover"

        # And the sampler must not re-enable fast copy on its own afterwards.
        time.sleep(0.5)
        assert _stack.fast_copy_memory_active() is False, "fast copy must stay disabled after a foreign takeover"
    finally:
        p.stop()

    print("OK")
