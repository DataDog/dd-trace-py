import sys

import pytest


CPU_TIMER_SKIP_REASON = "CPU timer profiler is enabled only on Linux Python 3.14+"
CPU_TIMER_SKIP = sys.platform != "linux" or sys.version_info < (3, 14)


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_cpu_timer_profiler",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
        "_DD_PROFILING_STACK_CPU_TIMER_ENABLED": "1",
        "_DD_PROFILING_STACK_CPU_TIMER_INTERVAL_MS": "5",
    },
    err=None,
)
@pytest.mark.skipif(CPU_TIMER_SKIP, reason=CPU_TIMER_SKIP_REASON)
def test_cpu_timer_profiler_emits_cpu_samples():
    import os
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils

    def cpu_timer_busy_loop():
        deadline = time.thread_time_ns() + 300_000_000
        value = 0
        while time.thread_time_ns() < deadline:
            value += 1
        return value

    p = profiler.Profiler()
    p.start()
    assert cpu_timer_busy_loop() > 0
    stats = stack._cpu_timer_debug_stats()
    p.stop()

    assert 0 < stats["tid_table_allocated_pages"] < stats["tid_table_directory_size"], stats

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    cpu_time_index = pprof_utils.get_sample_type_index(profile, "cpu-time")

    cpu_time_by_function = {}
    for sample in profile.sample:
        cpu_time_ns = sample.value[cpu_time_index]
        if cpu_time_ns <= 0:
            continue
        for location_id in sample.location_id:
            location = pprof_utils.get_location_with_id(profile, location_id)
            line = location.line[0]
            function = pprof_utils.get_function_with_id(profile, line.function_id)
            function_name = profile.string_table[function.name]
            cpu_time_by_function[function_name] = cpu_time_by_function.get(function_name, 0) + cpu_time_ns

    assert cpu_time_by_function.get("cpu_timer_busy_loop", 0) > 0, cpu_time_by_function


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_cpu_timer_independent_drain_cadence",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
        "_DD_PROFILING_STACK_CPU_TIMER_ENABLED": "1",
        "_DD_PROFILING_STACK_CPU_TIMER_INTERVAL_MS": "2",
    },
    err=None,
)
@pytest.mark.skipif(CPU_TIMER_SKIP, reason=CPU_TIMER_SKIP_REASON)
def test_cpu_timer_drain_cadence_does_not_override_wall_interval():
    import glob
    import json
    import os
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()
    stack.set_interval(0.1)

    deadline = time.thread_time_ns() + 500_000_000
    while time.thread_time_ns() < deadline:
        pass

    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    metadata_files = glob.glob(output_filename + ".*.internal_metadata.json")
    assert metadata_files

    sampling_event_count = 0
    total_cpu_time_us = 0
    wall_cpu_time_us = 0
    cpu_drain_time_us = 0
    for filename in metadata_files:
        with open(filename) as fp:
            metadata = json.load(fp)
        sampling_event_count += metadata["sampling_event_count"]
        total_cpu_time_us += metadata["sample_capture_cpu_time_us"]
        wall_cpu_time_us += metadata["wall_sample_capture_cpu_time_us"]
        cpu_drain_time_us += metadata["cpu_timer_drain_cpu_time_us"]

    # A 100 ms wall interval should produce only a handful of wall collection
    # events during this run. Coupling the full loop to the 2 ms CPU timer would
    # instead produce roughly 250 events.
    assert 2 <= sampling_event_count <= 15, sampling_event_count
    assert wall_cpu_time_us > 0
    assert cpu_drain_time_us > 0
    assert total_cpu_time_us >= wall_cpu_time_us + cpu_drain_time_us


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_cpu_timer_overrun_accounting",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
        "_DD_PROFILING_STACK_CPU_TIMER_ENABLED": "1",
        "_DD_PROFILING_STACK_CPU_TIMER_INTERVAL_MS": "1",
    },
    err=None,
)
@pytest.mark.skipif(CPU_TIMER_SKIP, reason=CPU_TIMER_SKIP_REASON)
def test_cpu_timer_reports_overrun_accounting_counters():
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler

    def cpu_timer_busy_loop():
        deadline = time.thread_time_ns() + 300_000_000
        value = 0
        while time.thread_time_ns() < deadline:
            value += 1
        return value

    p = profiler.Profiler()
    p.start()
    assert cpu_timer_busy_loop() > 0
    stats = stack._cpu_timer_debug_stats()
    p.stop()

    # The counters are always present and are non-negative integers. We do not assert they are
    # strictly positive: whether expirations coalesce depends on load and scheduling, so requiring
    # overruns would be flaky.
    assert "timer_overrun_total" in stats, stats
    assert "coalesced_signal_count" in stats, stats
    assert isinstance(stats["timer_overrun_total"], int) and stats["timer_overrun_total"] >= 0, stats
    assert isinstance(stats["coalesced_signal_count"], int) and stats["coalesced_signal_count"] >= 0, stats
    # Each coalesced signal contributes an si_overrun of at least 1, so the summed overruns can never
    # be smaller than the number of coalesced signals.
    assert stats["timer_overrun_total"] >= stats["coalesced_signal_count"], stats


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_cpu_timer_auxiliary_start_main_thread",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
        "_DD_PROFILING_STACK_CPU_TIMER_ENABLED": "1",
        "_DD_PROFILING_STACK_CPU_TIMER_INTERVAL_MS": "1",
    },
    err=None,
)
@pytest.mark.skipif(CPU_TIMER_SKIP, reason=CPU_TIMER_SKIP_REASON)
def test_cpu_timer_profiler_started_in_auxiliary_thread_profiles_main_thread():
    import os
    import threading
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils

    ready = threading.Event()
    stop = threading.Event()

    def profiler_thread():
        p = profiler.Profiler()
        p.start()
        ready.set()
        stop.wait(2.0)
        p.stop()

    def cpu_timer_main_thread_busy_loop():
        deadline = time.thread_time_ns() + 500_000_000
        value = 0
        while time.thread_time_ns() < deadline:
            value += 1
        return value

    thread = threading.Thread(target=profiler_thread, name="cpu-timer-profiler-starter")
    thread.start()
    assert ready.wait(2.0)
    try:
        time.sleep(0.2)
        assert cpu_timer_main_thread_busy_loop() > 0
        stats = stack._cpu_timer_debug_stats()
    finally:
        stop.set()
        thread.join(2.0)

    assert not thread.is_alive()
    assert stats["active"] is True, stats

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    cpu_time_index = pprof_utils.get_sample_type_index(profile, "cpu-time")

    cpu_time_by_function = {}
    for sample in profile.sample:
        cpu_time_ns = sample.value[cpu_time_index]
        if cpu_time_ns <= 0:
            continue
        for location_id in sample.location_id:
            location = pprof_utils.get_location_with_id(profile, location_id)
            line = location.line[0]
            function = pprof_utils.get_function_with_id(profile, line.function_id)
            function_name = profile.string_table[function.name]
            cpu_time_by_function[function_name] = cpu_time_by_function.get(function_name, 0) + cpu_time_ns

    assert cpu_time_by_function.get("cpu_timer_main_thread_busy_loop", 0) > 0, cpu_time_by_function


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_cpu_timer_asyncio_task_stitching",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
        "_DD_PROFILING_STACK_CPU_TIMER_ENABLED": "1",
        "_DD_PROFILING_STACK_CPU_TIMER_INTERVAL_MS": "1",
    },
    err=None,
)
@pytest.mark.skipif(CPU_TIMER_SKIP, reason=CPU_TIMER_SKIP_REASON)
def test_cpu_timer_profiler_stitches_asyncio_task_samples():
    import asyncio
    import os
    import time

    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils

    def cpu_timer_async_busy_loop():
        deadline = time.thread_time_ns() + 500_000_000
        value = 0
        while time.thread_time_ns() < deadline:
            value += 1
        return value

    async def main():
        task = asyncio.current_task()
        assert task is not None
        task.set_name("cpu-timer-async-main")

        p = profiler.Profiler()
        p.start()
        await asyncio.sleep(0)
        assert cpu_timer_async_busy_loop() > 0
        p.stop()

    asyncio.run(main())

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    cpu_time_index = pprof_utils.get_sample_type_index(profile, "cpu-time")

    stitched_cpu_samples = []
    task_cpu_sample_functions = []
    for sample in profile.sample:
        if sample.value[cpu_time_index] <= 0:
            continue
        task_name_label = pprof_utils.get_label_with_key(profile.string_table, sample, "task name")
        if task_name_label is None or profile.string_table[task_name_label.str] != "cpu-timer-async-main":
            continue
        function_names = set()
        for location_id in sample.location_id:
            location = pprof_utils.get_location_with_id(profile, location_id)
            line = location.line[0]
            function = pprof_utils.get_function_with_id(profile, line.function_id)
            function_names.add(profile.string_table[function.name])
        task_cpu_sample_functions.append(sorted(function_names))
        if "cpu_timer_async_busy_loop" in function_names:
            stitched_cpu_samples.append(sample)

    assert stitched_cpu_samples, task_cpu_sample_functions[:20]


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_cpu_timer_fault_guard",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
        "_DD_PROFILING_STACK_CPU_TIMER_ENABLED": "1",
        "_DD_PROFILING_STACK_CPU_TIMER_INTERVAL_MS": "1",
    },
    err=None,
)
@pytest.mark.skipif(CPU_TIMER_SKIP, reason=CPU_TIMER_SKIP_REASON)
def test_cpu_timer_guarded_frame_read_fault_does_not_crash():
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler

    def burn_cpu(duration_ns):
        deadline = time.thread_time_ns() + duration_ns
        value = 0
        while time.thread_time_ns() < deadline:
            value += 1
        return value

    stack._cpu_timer_debug_set_fault_injection(True)
    p = profiler.Profiler()
    p.start()
    assert burn_cpu(200_000_000) > 0

    stats = stack._cpu_timer_debug_stats()
    p.stop()

    assert stats["active"] is True, stats
    assert stats["live_count"] >= 1, stats
    assert stats["capture_failed_count"] > 0, stats
    assert stats["capture_failed_cpu_ns"] > 0, stats


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_cpu_timer_postfork",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
        "_DD_PROFILING_STACK_CPU_TIMER_ENABLED": "1",
        "_DD_PROFILING_STACK_CPU_TIMER_INTERVAL_MS": "1",
    },
    err=None,
)
@pytest.mark.skipif(CPU_TIMER_SKIP, reason=CPU_TIMER_SKIP_REASON)
def test_cpu_timer_postfork_child_reinitializes_without_stale_altstack():
    import os
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler

    def burn_cpu(duration_ns):
        deadline = time.thread_time_ns() + duration_ns
        value = 0
        while time.thread_time_ns() < deadline:
            value += 1
        return value

    p = profiler.Profiler()
    p.start()
    assert burn_cpu(50_000_000) > 0

    pid = os.fork()
    if pid == 0:
        try:
            assert burn_cpu(100_000_000) > 0
            stats = stack._cpu_timer_debug_stats()
            assert stats["active"] is True, stats
            assert stats["live_count"] >= 1, stats
            assert stats["leaked_altstack_count"] == 0, stats
        except BaseException:
            os._exit(1)
        os._exit(0)

    _, status = os.waitpid(pid, 0)
    p.stop()

    assert os.WIFEXITED(status), status
    assert os.WEXITSTATUS(status) == 0, status


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_cpu_timer_preexisting_thread",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
        "_DD_PROFILING_STACK_CPU_TIMER_ENABLED": "1",
        "_DD_PROFILING_STACK_CPU_TIMER_INTERVAL_MS": "1",
    },
    err=None,
)
@pytest.mark.skipif(CPU_TIMER_SKIP, reason=CPU_TIMER_SKIP_REASON)
def test_cpu_timer_preexisting_thread_remote_discovery_is_safe():
    import threading
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler

    ready = threading.Event()
    stop = threading.Event()

    def preexisting_worker():
        ready.set()
        while not stop.is_set():
            time.sleep(0.01)

    worker = threading.Thread(target=preexisting_worker, name="cpu-timer-preexisting")
    worker.start()
    assert ready.wait(1.0)

    p = profiler.Profiler()
    try:
        p.start()
        deadline = time.thread_time_ns() + 100_000_000
        value = 0
        while time.thread_time_ns() < deadline:
            value += 1
        assert value > 0
        stats = stack._cpu_timer_debug_stats()
    finally:
        stop.set()
        worker.join(1.0)
        p.stop()

    assert not worker.is_alive()
    assert stats["active"] is True, stats
    assert stats["live_count"] >= 1, stats
    assert stats["timer_syscall_failures"] == 0, stats
    assert stats["capture_failed_count"] == 0, stats


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_cpu_timer_thread_churn",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
        "_DD_PROFILING_STACK_CPU_TIMER_ENABLED": "1",
        "_DD_PROFILING_STACK_CPU_TIMER_INTERVAL_MS": "1",
    },
    err=None,
)
@pytest.mark.skipif(CPU_TIMER_SKIP, reason=CPU_TIMER_SKIP_REASON)
def test_cpu_timer_thread_churn_does_not_crash_or_leak_altstacks():
    import threading
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler

    def burn_cpu(duration_ns):
        deadline = time.thread_time_ns() + duration_ns
        value = 0
        while time.thread_time_ns() < deadline:
            value += 1
        return value

    rounds = 4
    workers_per_round = 8

    p = profiler.Profiler()
    p.start()
    for _ in range(rounds):
        workers = [threading.Thread(target=burn_cpu, args=(30_000_000,)) for _ in range(workers_per_round)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
    time.sleep(0.2)
    stats = stack._cpu_timer_debug_stats()
    p.stop()

    assert stats["active"] is True, stats
    assert stats["capture_failed_count"] == 0, stats
    # Two counters can rise benignly under heavy thread churn, so we bound them by the number of
    # churned threads instead of requiring zero:
    #   - timer_syscall_failures: a worker can exit between CPU-timer thread discovery and arming,
    #     so its per-thread timer_create/timer_settime syscall fails harmlessly.
    #   - leaked_altstack_count: when a worker is retired by another thread rather than itself, its
    #     signal alt-stack mapping is intentionally kept (not freed) so a late SIGPROF can never touch
    #     unmapped memory. This is a deliberate, process-lifetime-bounded deferral, not a real leak.
    # A systematic bug would instead surface as a crash (non-zero subprocess exit) or a count far above
    # one per churned thread.
    max_churn_races = rounds * workers_per_round
    assert stats["timer_syscall_failures"] <= max_churn_races, stats
    assert stats["leaked_altstack_count"] <= max_churn_races, stats


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_cpu_timer_stop_restart",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
        "_DD_PROFILING_STACK_CPU_TIMER_ENABLED": "1",
        "_DD_PROFILING_STACK_CPU_TIMER_INTERVAL_MS": "1",
    },
    err=None,
)
@pytest.mark.skipif(CPU_TIMER_SKIP, reason=CPU_TIMER_SKIP_REASON)
def test_cpu_timer_stop_then_restart_does_not_rearm_same_process():
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler

    def burn_cpu(duration_ns):
        deadline = time.thread_time_ns() + duration_ns
        value = 0
        while time.thread_time_ns() < deadline:
            value += 1
        return value

    p = profiler.Profiler()
    p.start()
    assert burn_cpu(100_000_000) > 0
    first_stats = stack._cpu_timer_debug_stats()
    p.stop()

    p = profiler.Profiler()
    p.start()
    assert burn_cpu(100_000_000) > 0
    restart_stats = stack._cpu_timer_debug_stats()
    p.stop()

    assert first_stats["active"] is True, first_stats
    assert first_stats["live_count"] >= 1, first_stats
    assert restart_stats["configured"] is True, restart_stats
    assert restart_stats["active"] is False, restart_stats
    assert restart_stats["replacing_wall_cpu"] is False, restart_stats
    assert restart_stats["live_count"] == 0, restart_stats


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_cpu_timer_blocked_fault_signal",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
        "_DD_PROFILING_STACK_CPU_TIMER_ENABLED": "1",
        "_DD_PROFILING_STACK_CPU_TIMER_INTERVAL_MS": "1",
    },
    err=None,
)
@pytest.mark.skipif(CPU_TIMER_SKIP, reason=CPU_TIMER_SKIP_REASON)
def test_cpu_timer_does_not_arm_when_fault_signal_is_blocked():
    import signal
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler

    old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGBUS})
    try:
        p = profiler.Profiler()
        p.start()
        time.sleep(0.1)
        stats = stack._cpu_timer_debug_stats()
        p.stop()
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)

    assert stats["blocked_signal_count"] > 0, stats
    assert stats["live_count"] == 0, stats
    assert stats["replacing_wall_cpu"] is False, stats


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_cpu_timer_syscall_readv",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
        "_DD_PROFILING_STACK_CPU_TIMER_ENABLED": "1",
        "_DD_PROFILING_STACK_CPU_TIMER_INTERVAL_MS": "1",
    },
    err=None,
)
@pytest.mark.skipif(CPU_TIMER_SKIP, reason=CPU_TIMER_SKIP_REASON)
def test_cpu_timer_syscall_readv_loop_does_not_surface_eintr():
    import os
    import threading
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils

    errors = []
    iterations = [0]

    def readv_loop():
        fd = os.open("/dev/zero", os.O_RDONLY)
        try:
            buf = bytearray(1024 * 1024)
            view = memoryview(buf)
            deadline = time.monotonic() + 0.8
            local_iterations = 0
            while time.monotonic() < deadline:
                try:
                    read = os.readv(fd, [view])
                except InterruptedError as exc:
                    errors.append(repr(exc))
                    break
                except OSError as exc:
                    errors.append(repr(exc))
                    break
                if read != len(buf):
                    errors.append(f"short read: {read}")
                    break
                local_iterations += 1
            iterations[0] = local_iterations
        finally:
            os.close(fd)

    p = profiler.Profiler()
    p.start()
    worker = threading.Thread(target=readv_loop, name="cpu-timer-readv-loop")
    worker.start()
    worker.join()
    stats = stack._cpu_timer_debug_stats()
    p.stop()

    assert errors == []
    assert iterations[0] > 0
    assert stats["active"] is True, stats
    assert stats["timer_syscall_failures"] == 0, stats
    assert stats["capture_failed_count"] == 0, stats

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    cpu_time_index = pprof_utils.get_sample_type_index(profile, "cpu-time")
    readv_loop_cpu_ns = 0
    for sample in profile.sample:
        cpu_time_ns = sample.value[cpu_time_index]
        if cpu_time_ns <= 0:
            continue
        for location_id in sample.location_id:
            location = pprof_utils.get_location_with_id(profile, location_id)
            line = location.line[0]
            function = pprof_utils.get_function_with_id(profile, line.function_id)
            function_name = profile.string_table[function.name]
            if function_name == "readv_loop":
                readv_loop_cpu_ns += cpu_time_ns

    assert readv_loop_cpu_ns > 0


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_cpu_timer_blocked_signal",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
        "_DD_PROFILING_STACK_CPU_TIMER_ENABLED": "1",
        "_DD_PROFILING_STACK_CPU_TIMER_INTERVAL_MS": "1",
    },
    err=None,
)
@pytest.mark.skipif(CPU_TIMER_SKIP, reason=CPU_TIMER_SKIP_REASON)
def test_cpu_timer_does_not_arm_when_sigprof_is_blocked():
    import signal
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler

    old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGPROF})
    try:
        p = profiler.Profiler()
        p.start()
        time.sleep(0.1)
        stats = stack._cpu_timer_debug_stats()
        p.stop()
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)

    assert stats["blocked_signal_count"] > 0, stats
    assert stats["live_count"] == 0, stats


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_cpu_timer_foreign_sigprof",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
        "_DD_PROFILING_STACK_CPU_TIMER_ENABLED": "1",
        "_DD_PROFILING_STACK_CPU_TIMER_INTERVAL_MS": "1",
    },
    err=None,
)
@pytest.mark.skipif(CPU_TIMER_SKIP, reason=CPU_TIMER_SKIP_REASON)
def test_cpu_timer_forwards_foreign_sigprof_to_previous_handler():
    import os
    import signal
    import time

    from ddtrace.profiling import profiler

    seen = []

    def handle_sigprof(signum, frame):
        seen.append(signum)

    old_handler = signal.signal(signal.SIGPROF, handle_sigprof)
    try:
        p = profiler.Profiler()
        p.start()
        os.kill(os.getpid(), signal.SIGPROF)
        deadline = time.time() + 1.0
        while not seen and time.time() < deadline:
            time.sleep(0.01)
        p.stop()
    finally:
        signal.signal(signal.SIGPROF, old_handler)

    assert seen == [signal.SIGPROF], seen


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_cpu_timer_sigprof_hijack",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
        "_DD_PROFILING_STACK_CPU_TIMER_ENABLED": "1",
        "_DD_PROFILING_STACK_CPU_TIMER_INTERVAL_MS": "1",
    },
    err=None,
)
@pytest.mark.skipif(CPU_TIMER_SKIP, reason=CPU_TIMER_SKIP_REASON)
def test_cpu_timer_disables_when_sigprof_handler_is_replaced():
    import signal
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler

    def burn_cpu(duration_ns):
        deadline = time.thread_time_ns() + duration_ns
        value = 0
        while time.thread_time_ns() < deadline:
            value += 1
        return value

    p = profiler.Profiler()
    p.start()
    assert burn_cpu(100_000_000) > 0

    signal.signal(signal.SIGPROF, lambda signum, frame: None)
    time.sleep(0.2)
    stats = stack._cpu_timer_debug_stats()
    p.stop()

    assert stats["handler_hijack_disable_count"] > 0, stats
    assert stats["permanently_disabled"] is True, stats
    assert stats["active"] is False, stats
    assert stats["replacing_wall_cpu"] is False, stats


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_cpu_timer_sigbus_hijack",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
        "_DD_PROFILING_STACK_CPU_TIMER_ENABLED": "1",
        "_DD_PROFILING_STACK_CPU_TIMER_INTERVAL_MS": "1",
    },
    err=None,
)
@pytest.mark.skipif(CPU_TIMER_SKIP, reason=CPU_TIMER_SKIP_REASON)
def test_cpu_timer_disables_when_fault_handler_is_replaced():
    import signal
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()

    old_handler = signal.getsignal(signal.SIGBUS)
    signal.signal(signal.SIGBUS, lambda signum, frame: None)
    time.sleep(0.2)
    stats = stack._cpu_timer_debug_stats()
    signal.signal(signal.SIGBUS, old_handler)
    p.stop()

    assert stats["handler_hijack_disable_count"] > 0, stats
    assert stats["permanently_disabled"] is True, stats
    assert stats["active"] is False, stats
    assert stats["replacing_wall_cpu"] is False, stats
