import sys

import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_copy_memory_error_count",
        DD_PROFILING_UPLOAD_INTERVAL="1",
    ),
    err=None,
)
def test_copy_memory_error_count_present() -> None:
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


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_fast_copy_memory_disabled",
        DD_PROFILING_UPLOAD_INTERVAL="1",
        _DD_PROFILING_STACK_FAST_COPY="false",
    ),
    err=None,
)
def test_fast_copy_memory_disabled() -> None:
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


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_fast_copy_memory_enabled",
        DD_PROFILING_UPLOAD_INTERVAL="1",
        _DD_PROFILING_STACK_FAST_COPY="1",
    ),
    err=None,
)
def test_fast_copy_memory_enabled() -> None:
    """Sampler runs on the syscall copy during warmup, then upgrades to safe_memcpy."""
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


@pytest.mark.skipif(sys.platform == "win32", reason="fork/signal tests not supported on Windows")
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_fast_copy_faulthandler_warmup",
        DD_PROFILING_UPLOAD_INTERVAL="1",
        _DD_PROFILING_STACK_FAST_COPY="1",
    ),
    err=None,
)
def test_fast_copy_faulthandler_enable_during_warmup() -> None:
    """faulthandler.enable() inside the warmup window must not cost us the handler."""
    import faulthandler

    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_utils import wait_for_fast_copy_state

    _stack._set_fast_copy_warmup_seconds(3.0)

    p: profiler.Profiler = profiler.Profiler(tracer=tracer)
    p.start()

    # Inside warmup: inactive but handlers still installed.
    assert wait_for_fast_copy_state(_stack, False), "sampler never dropped to the syscall copy"

    # Hooks must reclaim during warmup (were gated on fast_copy_active).
    faulthandler.enable()
    assert _stack.segv_handler_installed(), "handler not reclaimed after faulthandler.enable()"

    upgraded: bool = wait_for_fast_copy_state(_stack, True, timeout=20.0)
    p.stop()

    assert upgraded, "faulthandler.enable() during warmup pinned the process to the syscall copy"


@pytest.mark.skipif(sys.platform == "win32", reason="fork/signal tests not supported on Windows")
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_fast_copy_fork_during_warmup",
        DD_PROFILING_UPLOAD_INTERVAL="1",
        _DD_PROFILING_STACK_FAST_COPY="1",
    ),
    err=None,
)
def test_fast_copy_fork_during_warmup() -> None:
    """A child forked mid-warmup re-runs the warmup decision rather than inheriting it."""
    import os

    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_utils import wait_for_fast_copy_state

    _stack._set_fast_copy_warmup_seconds(3.0)

    p: profiler.Profiler = profiler.Profiler(tracer=tracer)
    p.start()

    # Fork mid-warmup (gunicorn/celery shape).
    assert wait_for_fast_copy_state(_stack, False), "sampler never dropped to the syscall copy"

    pid: int = os.fork()
    if pid == 0:
        # atfork restarts sampler; must not inherit fast_copy_active==false forever.
        try:
            child_upgraded: bool = wait_for_fast_copy_state(_stack, True, timeout=20.0)
        except BaseException:
            os._exit(2)
        os._exit(0 if child_upgraded else 1)

    status: int
    _, status = os.waitpid(pid, 0)
    p.stop()

    assert os.WIFEXITED(status), f"child did not exit normally: {status}"
    assert os.WEXITSTATUS(status) == 0, "child forked mid-warmup never upgraded to safe_memcpy"


@pytest.mark.skipif(sys.platform == "win32", reason="signal tests not supported on Windows")
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_fast_copy_faulthandler_after_foreign_warmup",
        DD_PROFILING_UPLOAD_INTERVAL="1",
        _DD_PROFILING_STACK_FAST_COPY="1",
    ),
    err=None,
)
def test_fast_copy_faulthandler_enable_after_foreign_during_warmup() -> None:
    """Foreign handler then faulthandler.enable during warmup must not reclaim.

    If a foreign handler sits on top of ours, uninstall is a no-op. Reclaiming after
    faulthandler.enable would loop (us -> faulthandler -> foreign -> us) and the
    warmup deadline would upgrade to safe_memcpy over that chain.
    """
    import faulthandler
    import signal
    import time

    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_utils import wait_for_fast_copy_state

    _stack._set_fast_copy_warmup_seconds(3.0)

    p: profiler.Profiler = profiler.Profiler(tracer=tracer)
    p.start()

    assert wait_for_fast_copy_state(_stack, False), "sampler never dropped to the syscall copy"

    signal.signal(signal.SIGSEGV, signal.SIG_DFL)
    assert _stack.segv_handler_installed() is False, "expected foreign takeover of SIGSEGV"

    faulthandler.enable()
    assert _stack.segv_handler_installed() is False, (
        "reclaimed over foreign handler after faulthandler.enable during warmup"
    )

    # Past the warmup deadline: must stay on syscall copy, not upgrade.
    time.sleep(4)
    assert _stack.fast_copy_memory_active() is False, "upgraded to safe_memcpy after foreign+faulthandler during warmup"
    p.stop()


@pytest.mark.skipif(sys.platform != "linux", reason="process_vm_readv fallback is Linux-only")
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_fast_copy_faulthandler_no_syscall",
        DD_PROFILING_UPLOAD_INTERVAL="1",
        _DD_PROFILING_STACK_FAST_COPY="1",
    ),
    err=None,
)
def test_fast_copy_faulthandler_enable_stops_when_no_syscall_fallback() -> None:
    """Foreign takeover + faulthandler.enable with no process_vm_readv must stop sampling.

    Uninstall marks sticky takeover; if dropping to the syscall copy fails, resume must
    not continue with leftover safe_memcpy under a foreign handler (handler checks are
    skipped once sticky is set).
    """
    import faulthandler
    import signal

    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_utils import wait_for_fast_copy_state

    # No process_vm_readv: skip warmup, stay on safe_memcpy, leave no syscall fallback.
    _stack._set_process_vm_readv_available(False)

    p: profiler.Profiler = profiler.Profiler(tracer=tracer)
    p.start()

    assert wait_for_fast_copy_state(_stack, True, timeout=20.0), "sampler never reached safe_memcpy"

    signal.signal(signal.SIGSEGV, signal.SIG_DFL)
    assert _stack.segv_handler_installed() is False, "expected foreign takeover of SIGSEGV"

    faulthandler.enable()

    assert _stack.is_safe_copy_failed(), "expected failed_safe_copy when uninstall cannot fall back"
    # pause_sampling returns False when the sampler is not running (stopped in uninstall).
    assert _stack.pause_sampling() is False, "sampler should have stopped instead of resuming into memcpy"
    p.stop()


@pytest.mark.skipif(sys.platform == "win32", reason="signal tests not supported on Windows")
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_fast_copy_foreign_takeover",
        DD_PROFILING_UPLOAD_INTERVAL="1",
        _DD_PROFILING_STACK_FAST_COPY="1",
    ),
    err=None,
)
def test_fast_copy_foreign_handler_takeover_metadata() -> None:
    """Foreign SIGSEGV takeover sticks across stop/set_fast_copy/restart."""
    import json
    import os
    import signal
    import time
    from typing import Any
    from typing import Optional

    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_utils import wait_for_fast_copy_state

    _stack._set_fast_copy_warmup_seconds(2.0)

    p: profiler.Profiler = profiler.Profiler(tracer=tracer)
    p.start()

    # Takeover during warmup before upgrade.
    assert wait_for_fast_copy_state(_stack, False), "sampler never dropped to the syscall copy"

    signal.signal(signal.SIGSEGV, signal.SIG_DFL)
    assert _stack.segv_handler_installed() is False, "expected foreign takeover of SIGSEGV"

    # Past warmup + upload interval for metadata flush.
    time.sleep(4)
    p.stop()

    output_filename: str = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    files: list[str] = pprof_utils.get_internal_metadata_files(output_filename)
    assert files, "Expected at least one internal_metadata.json file"

    metadata: Optional[dict[str, Any]] = None
    for f in reversed(files):
        with open(f) as fp:
            candidate: dict[str, Any] = json.load(fp)

        if candidate.get("sampling_event_count", 0) > 0:
            metadata = candidate
            break

    assert metadata is not None, f"Expected an upload window with at least one sampling cycle: {files}"

    assert metadata["fast_copy_memory_desired"] is True, metadata
    assert metadata["fast_copy_memory_foreign_takeover"] is True, metadata
    assert metadata["fast_copy_memory_syscall_fallback"] is True, metadata
    assert metadata["fast_copy_memory_enabled"] is False, metadata

    # Sticky: survives stop -> set_fast_copy(True) -> start.
    _stack.set_fast_copy(True)
    p.start()
    assert _stack.fast_copy_memory_active() is False, "foreign takeover must block fast-copy re-enable after restart"

    time.sleep(2)
    p.stop()

    # Exclude first-run uploads so a quiet restart cannot pass by re-reading them.
    first_run_files: set[str] = set(files)
    restart_files: list[str] = [
        f for f in pprof_utils.get_internal_metadata_files(output_filename) if f not in first_run_files
    ]
    assert restart_files, "Expected at least one new internal_metadata.json file after restart"

    restart_metadata: Optional[dict[str, Any]] = None
    for f in reversed(restart_files):
        with open(f) as fp:
            restart_candidate: dict[str, Any] = json.load(fp)

        if restart_candidate.get("sampling_event_count", 0) > 0:
            restart_metadata = restart_candidate
            break

    assert restart_metadata is not None, (
        f"Expected an upload window with at least one sampling cycle after restart: {restart_files}"
    )
    assert restart_metadata["fast_copy_memory_foreign_takeover"] is True, restart_metadata
    assert restart_metadata["fast_copy_memory_enabled"] is False, restart_metadata


@pytest.mark.skipif(sys.platform != "linux", reason="process_vm_readv fallback is Linux-only")
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_fast_copy_fork_takeover_no_syscall",
        DD_PROFILING_UPLOAD_INTERVAL="1",
        _DD_PROFILING_STACK_FAST_COPY="1",
    ),
    err=None,
)
def test_fast_copy_fork_after_takeover_without_syscall() -> None:
    """Child must not sample leftover safe_memcpy after a no-syscall foreign takeover."""
    import json
    import os
    import signal
    import time
    from typing import Any
    from typing import Optional

    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_utils import wait_for_fast_copy_state

    # No process_vm_readv before start: skips warmup, stays on safe_memcpy, and
    # leaves no syscall fallback when a foreign handler takes SIGSEGV later.
    _stack._set_process_vm_readv_available(False)

    p: profiler.Profiler = profiler.Profiler(tracer=tracer)
    p.start()

    assert wait_for_fast_copy_state(_stack, True, timeout=20.0), "sampler never reached safe_memcpy"

    signal.signal(signal.SIGSEGV, signal.SIG_DFL)
    assert _stack.segv_handler_installed() is False, "expected foreign takeover of SIGSEGV"

    # Parent sampling thread should stop (no syscall fallback). Give it a cycle.
    deadline: float = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if _stack.is_safe_copy_failed():
            break
        time.sleep(0.05)
    assert _stack.is_safe_copy_failed(), "expected failed_safe_copy after takeover with no process_vm_readv"

    pid: int = os.fork()
    if pid == 0:
        # Parent already stopped (failed_safe_copy), so restart_after_fork is a no-op and
        # origin-task linking stays off. Assert memcpy independently: an `and restarted`
        # check would miss leftover fast_copy_active after a failed disable.
        try:
            memcpy_on: bool = _stack.fast_copy_memory_active()
            time.sleep(0.5)
        except BaseException:
            os._exit(2)
        os._exit(1 if memcpy_on else 0)

    status: int
    _, status = os.waitpid(pid, 0)
    p.stop()

    assert os.WIFEXITED(status), f"child did not exit normally: {status}"
    assert os.WEXITSTATUS(status) == 0, "child sampled leftover safe_memcpy after no-syscall takeover"

    # Failed disable breaks before the end-of-cycle stats update; metadata must still
    # reflect that fast copy is off (not the pre-disable snapshot from mark takeover).
    output_filename: str = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    files: list[str] = pprof_utils.get_internal_metadata_files(output_filename)
    assert files, "Expected at least one internal_metadata.json file"

    metadata: Optional[dict[str, Any]] = None
    for f in reversed(files):
        with open(f) as fp:
            candidate: dict[str, Any] = json.load(fp)
        if candidate.get("fast_copy_memory_foreign_takeover") is True:
            metadata = candidate
            break

    assert metadata is not None, f"Expected a foreign-takeover metadata window: {files}"
    assert metadata["fast_copy_memory_enabled"] is False, metadata


@pytest.mark.skipif(sys.platform != "linux", reason="process_vm_readv fallback is Linux-only")
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_fast_copy_reenable_clears_failed_safe_copy",
        DD_PROFILING_UPLOAD_INTERVAL="1",
        _DD_PROFILING_STACK_FAST_COPY="1",
    ),
    err=None,
)
def test_fast_copy_reenable_clears_failed_safe_copy() -> None:
    """set_fast_copy(True) must clear failed_safe_copy so start() can recover.

    With no process_vm_readv, set_fast_copy(False) latches failed_safe_copy and
    drops any leftover recovery handler (stop leaves it installed). A later
    set_fast_copy(True) that restores safe_memcpy must clear that latch; otherwise
    Sampler::start() keeps returning False even though a valid copy method is active.
    """
    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_utils import wait_for_fast_copy_state

    _stack._set_process_vm_readv_available(False)

    p: profiler.Profiler = profiler.Profiler(tracer=tracer)
    p.start()
    assert wait_for_fast_copy_state(_stack, True, timeout=20.0), "sampler never reached safe_memcpy"
    p.stop()
    assert _stack.segv_handler_installed(), "stop should leave the recovery handler installed"

    _stack.set_fast_copy(False)
    assert _stack.is_safe_copy_failed(), "expected failed_safe_copy after disable with no process_vm_readv"
    assert not _stack.segv_handler_installed(), "failed disable must uninstall leftover recovery handler"

    _stack.set_fast_copy(True)
    assert not _stack.is_safe_copy_failed(), "re-enable must clear failed_safe_copy when safe_memcpy is usable"
    assert _stack.segv_handler_installed(), (
        "re-enable must reinstall the recovery handler after failed-disable uninstall"
    )

    p.start()
    assert wait_for_fast_copy_state(_stack, True, timeout=20.0), "sampler failed to restart after re-enable"
    p.stop()


@pytest.mark.skipif(sys.platform == "win32", reason="fast-copy recovery handlers are not used on Windows")
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_fast_copy_successful_disable_uninstalls_handler",
        DD_PROFILING_UPLOAD_INTERVAL="1",
        _DD_PROFILING_STACK_FAST_COPY="1",
    ),
    err=None,
)
def test_fast_copy_successful_disable_uninstalls_handler() -> None:
    """Successful set_fast_copy(False) must drop a dormant recovery handler.

    After disable, handler ops are off so uninstall_segv_handler is a no-op for
    coordinated installs. Leaving the handler installed lets faulthandler chain
    to it; set_fast_copy(True) would then reclaim over that chain and form a cycle.
    """
    import faulthandler

    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_utils import wait_for_fast_copy_state

    p: profiler.Profiler = profiler.Profiler(tracer=tracer)
    p.start()
    assert wait_for_fast_copy_state(_stack, True, timeout=20.0), "sampler never reached safe_memcpy"
    p.stop()
    assert _stack.segv_handler_installed(), "stop should leave the recovery handler installed"

    _stack.set_fast_copy(False)
    assert not _stack.is_safe_copy_failed(), "platform syscall fallback should make disable succeed"
    assert not _stack.segv_handler_installed(), "successful disable must uninstall the dormant recovery handler"

    faulthandler.enable()
    _stack.set_fast_copy(True)
    assert _stack.segv_handler_installed(), "re-enable must reclaim cleanly after faulthandler.enable()"

    p.start()
    assert wait_for_fast_copy_state(_stack, True, timeout=20.0), "sampler failed to restart after re-enable"
    p.stop()


@pytest.mark.skipif(sys.platform == "win32", reason="fast-copy recovery handlers are not used on Windows")
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_fast_copy_foreign_while_stopped",
        DD_PROFILING_UPLOAD_INTERVAL="1",
        _DD_PROFILING_STACK_FAST_COPY="1",
    ),
    err=None,
)
def test_fast_copy_set_true_does_not_overwrite_foreign_while_stopped() -> None:
    """set_fast_copy(True) must not reclaim over a foreign owner after stop.

    Stop leaves our handlers installed. A library can take SIGSEGV/SIGBUS before
    the next start; overwriting that owner would build us→foreign→us and skip
    sticky takeover. Fall back instead.
    """
    import signal

    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_utils import wait_for_fast_copy_state

    def _foreign_handler(signum: int, frame: object) -> None:
        return None

    p: profiler.Profiler = profiler.Profiler(tracer=tracer)
    p.start()
    assert wait_for_fast_copy_state(_stack, True, timeout=20.0), "sampler never reached safe_memcpy"
    p.stop()
    assert _stack.segv_handler_installed(), "stop should leave the recovery handler installed"

    signal.signal(signal.SIGSEGV, _foreign_handler)
    signal.signal(signal.SIGBUS, _foreign_handler)
    assert _stack.segv_handler_installed() is False, "expected foreign owner after stop"

    _stack.set_fast_copy(True)
    assert _stack.segv_handler_installed() is False, "set_fast_copy(True) must not overwrite a foreign owner"
    assert _stack.fast_copy_memory_active() is False, "foreign owner must force syscall fallback"

    p.start()
    assert _stack.fast_copy_memory_active() is False, "sticky foreign takeover must block safe_memcpy after restart"
    p.stop()
