"""
Regression test: worker threads created after fork must appear in profiles.

Scenario under test:
  - Profiler starts in master before fork (admission injection / preload_app=True)
  - Worker creates threads after fork (gthread pattern)
  - Worker threads must appear in wall-time samples, not just MainThread

Guards against:
  - PR #17183: incorrect pthread_atfork ordering between ddup and the stack
    sampler caused the sampling thread to race against ddup's Profile reset,
    producing empty or corrupt worker profiles (only MainThread visible).
  - PR #17042: fork mid-LRU-cache mutation caused SIGABRT in workers.
  - PR #18063: stale string tables post-fork caused SIGSEGV in workers.

Failure signature on affected versions (v4.7.x):
  - Worker profiles are empty, or
  - Only "MainThread" appears in wall-time samples.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

from tests.profiling.collector import pprof_utils


@pytest.mark.skipif(sys.platform == "win32", reason="os.fork() not available on Windows")
def test_worker_threads_appear_in_fork_child_profiles(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pprof_prefix = str(tmp_path / "preload_fork")

    env = {
        **os.environ,
        "DD_PROFILING_ENABLED": "1",
        "DD_PROFILING_OUTPUT_PPROF": pprof_prefix,
        # Disable adaptive sampling so every interval produces samples.
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    }

    script = os.path.join(os.path.dirname(__file__), "preload_fork_gthread.py")
    result = subprocess.run(
        [sys.executable, script],
        env=env,
        timeout=30,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"Script exited with code {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )

    child_pid = int(result.stdout.strip())

    # Parse the worker's pprof — the child PID is embedded in the filename.
    profile = pprof_utils.parse_newest_profile(
        f"{pprof_prefix}.{child_pid}",
        allow_penultimate=True,
    )

    wall_time_samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    assert len(wall_time_samples) > 0, (
        f"Worker (pid={child_pid}) produced no wall-time samples. "
        "The profiler may not have started in the child process."
    )

    # Collect every thread name that appears across wall-time samples.
    thread_names: set[str] = set()
    for sample in wall_time_samples:
        label = pprof_utils.get_label_with_key(profile.string_table, sample, "thread name")
        if label is not None:
            thread_names.add(profile.string_table[label.str])

    worker_thread_names = {n for n in thread_names if n != "MainThread"}
    assert worker_thread_names, (
        f"Only 'MainThread' appeared in worker wall-time samples (all names: {thread_names}). "
        "Expected WorkerThread-N threads created after fork to be visible. "
        "This indicates a profiler fork ordering bug — see PRs #17183, #17042."
    )


def test_ddup_atfork_handler_registered_before_stack_sampler(monkeypatch: pytest.MonkeyPatch) -> None:
    """ddup.start() must be called before stack.start() so that ddup's pthread_atfork
    child handler is registered first.

    POSIX guarantees FIFO ordering for post-fork child handlers, so ddup's handler
    runs first — resetting the profile state — before the stack sampler clears and
    rebuilds its thread map.  If the order is reversed, the stack sampler wipes the
    thread map before ddup has rebuilt the profile, leaving only MainThread visible
    in fork-child profiles.

    Regression guard for PRs #17183, #17042, #18063.
    """
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling import profiler as prof_module
    from ddtrace.profiling.collector import stack as stack_collector_module

    call_order: list[str] = []

    original_ddup_start = ddup.start

    def recording_ddup_start(*args, **kwargs):
        call_order.append("ddup.start")
        return original_ddup_start(*args, **kwargs)

    monkeypatch.setattr(ddup, "start", recording_ddup_start)

    # Stub _init to record when the stack sampler would register its pthread_atfork
    # handler (via stack.start → Sampler::one_time_setup) without starting real threads.
    def recording_stack_init(self) -> None:
        call_order.append("stack._init")

    monkeypatch.setattr(stack_collector_module.StackCollector, "_init", recording_stack_init)
    monkeypatch.setattr(stack_collector_module.StackCollector, "_stop_service", lambda self: None)

    p = prof_module.Profiler()
    p.start()
    p.stop(flush=False)

    assert "ddup.start" in call_order, "ddup.start() was never called during profiler startup"
    assert "stack._init" in call_order, "StackCollector._init() was never called during profiler startup"

    ddup_idx = call_order.index("ddup.start")
    stack_idx = call_order.index("stack._init")
    assert ddup_idx < stack_idx, (
        f"ddup.start() must come before StackCollector._init() so that ddup's "
        f"pthread_atfork child handler is registered first (POSIX FIFO ordering). "
        f"Got call order: {call_order}. "
        "Regression of PRs #17183/#17042/#18063."
    )
