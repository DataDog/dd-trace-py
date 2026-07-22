import sys

import pytest


@pytest.mark.subprocess(env={"DD_PROFILING_STACK_GC_ENABLED": "true", "PYTHONFAULTHANDLER": "1"})
def test_gc_callback_lifecycle_is_idempotent_and_preserves_user_callbacks() -> None:
    import gc
    import pathlib
    import sys
    import tempfile

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector.gc_utils import ddtrace_gc_callbacks

    pprof_prefix = str(pathlib.Path(tempfile.mkdtemp()) / "test_gc_callback_lifecycle")
    ddup.config(env="test", service="test_gc_callback_lifecycle", version="1.0", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def user_callback(phase, info):
        pass

    gc.callbacks.append(user_callback)
    try:
        for _ in range(2):
            collector = stack.StackCollector()
            collector.start()
            if sys.version_info < (3, 15):
                assert len(ddtrace_gc_callbacks(gc)) == 1
            else:
                assert not ddtrace_gc_callbacks(gc)
            assert user_callback in gc.callbacks

            collector.stop()
            assert not ddtrace_gc_callbacks(gc)
            assert user_callback in gc.callbacks
    finally:
        gc.callbacks.remove(user_callback)


@pytest.mark.subprocess(env={"DD_PROFILING_STACK_GC_ENABLED": "false", "PYTHONFAULTHANDLER": "1"})
def test_gc_callback_is_not_installed_when_disabled() -> None:
    import gc
    import pathlib
    import tempfile

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector.gc_utils import ddtrace_gc_callbacks

    pprof_prefix = str(pathlib.Path(tempfile.mkdtemp()) / "test_gc_callback_disabled")
    ddup.config(env="test", service="test_gc_callback_disabled", version="1.0", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    with stack.StackCollector():
        assert not ddtrace_gc_callbacks(gc)


@pytest.mark.skipif(sys.platform != "linux" or sys.version_info >= (3, 15), reason="fallback callback startup test")
@pytest.mark.subprocess(env={"PYTHONFAULTHANDLER": "1"}, err=None)
def test_gc_callback_is_removed_when_native_sampler_start_fails() -> None:
    import gc
    import pathlib
    import resource
    import tempfile

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.internal.datadog.profiling import stack
    from tests.profiling.collector.gc_utils import ddtrace_gc_callbacks

    pprof_prefix = str(pathlib.Path(tempfile.mkdtemp()) / "test_gc_callback_failed_start")
    ddup.config(env="test", service="test_gc_callback_failed_start", version="1.0", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    _, hard_limit = resource.getrlimit(resource.RLIMIT_STACK)
    # pthread_attr_setstacksize accepts this page-aligned value, but pthread_create
    # cannot reserve an exabyte-sized virtual address range and returns failure.
    resource.setrlimit(resource.RLIMIT_STACK, (1 << 60, hard_limit))
    stack.set_gc_enabled(True)
    started = stack.start()
    if started:
        stack.stop()

    assert started is False
    assert not ddtrace_gc_callbacks(gc)


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_STACK_GC_ENABLED": "true",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
        "_DD_PROFILING_STACK_MAX_THREADS": "0",
    }
)
def test_gc_frame_precedes_triggering_frame_and_is_limited_to_collecting_thread() -> None:
    import _thread
    import os
    import pathlib
    import tempfile
    import threading
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.gc_utils import gc_samples
    from tests.profiling.collector.gc_utils import slow_cyclic_collection

    test_name = "test_gc_frame_precedes_triggering_frame"
    pprof_prefix = str(pathlib.Path(tempfile.mkdtemp()) / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    ddup.config(env="test", service=test_name, version="1.0", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    collecting_thread = {"id": None}

    def collect_cycles() -> None:
        collecting_thread["id"] = _thread.get_ident()
        slow_cyclic_collection()

    stop = threading.Event()

    def non_collecting_worker() -> None:
        while not stop.is_set():
            time.sleep(0.01)

    other = threading.Thread(target=non_collecting_worker, name="non-collecting-thread")
    collecting = threading.Thread(target=collect_cycles, name="collecting-thread")

    with stack.StackCollector():
        other.start()
        collecting.start()
        collecting.join(timeout=5)
        stop.set()
        other.join(timeout=5)

    assert not collecting.is_alive()
    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = gc_samples(profile, pprof_utils)
    assert samples

    for sample in samples:
        thread_id = pprof_utils.get_label_with_key(profile.string_table, sample, "thread id")
        thread_name = pprof_utils.get_label_with_key(profile.string_table, sample, "thread name")
        assert thread_id is not None and thread_id.num == collecting_thread["id"]
        assert thread_name is not None and profile.string_table[thread_name.str] == "collecting-thread"

        locations = [pprof_utils.get_location_from_id(profile, location_id) for location_id in sample.location_id]
        gc_index = next(i for i, location in enumerate(locations) if location.function_name == "Garbage collection")
        assert locations[gc_index].filename == "<runtime>"
        assert locations[gc_index].line_no == 0
        assert locations[gc_index + 1].function_name.endswith("slow_cyclic_collection")

        raw_gc_location = pprof_utils.get_location_with_id(profile, sample.location_id[gc_index])
        assert raw_gc_location.address == 0


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_STACK_GC_ENABLED": "false",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
        "_DD_PROFILING_STACK_MAX_THREADS": "0",
    }
)
def test_disabled_profile_has_no_gc_frame() -> None:
    import os
    import pathlib
    import tempfile

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.gc_utils import gc_samples
    from tests.profiling.collector.gc_utils import slow_cyclic_collection

    test_name = "test_disabled_profile_has_no_gc_frame"
    pprof_prefix = str(pathlib.Path(tempfile.mkdtemp()) / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    ddup.config(env="test", service=test_name, version="1.0", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    with stack.StackCollector():
        slow_cyclic_collection()

    ddup.upload()
    profile = pprof_utils.parse_newest_profile(output_filename)
    assert not gc_samples(profile, pprof_utils)


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_STACK_GC_ENABLED": "true",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
        "_DD_PROFILING_STACK_MAX_THREADS": "1",
    }
)
def test_gc_frame_survives_thread_reservoir_sampling() -> None:
    import os
    import pathlib
    import tempfile

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.gc_utils import gc_samples
    from tests.profiling.collector.gc_utils import slow_cyclic_collection

    test_name = "test_gc_frame_survives_thread_reservoir_sampling"
    pprof_prefix = str(pathlib.Path(tempfile.mkdtemp()) / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    ddup.config(env="test", service=test_name, version="1.0", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    with stack.StackCollector():
        slow_cyclic_collection(1.5)

    ddup.upload()
    profile = pprof_utils.parse_newest_profile(output_filename)
    assert gc_samples(profile, pprof_utils)


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_STACK_GC_ENABLED": "true",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
        "_DD_PROFILING_STACK_MAX_THREADS": "0",
    }
)
def test_gc_frame_is_limited_to_on_cpu_asyncio_task() -> None:
    import asyncio
    import os
    import pathlib
    import tempfile

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.gc_utils import gc_samples
    from tests.profiling.collector.gc_utils import slow_cyclic_collection

    test_name = "test_gc_frame_is_limited_to_on_cpu_asyncio_task"
    pprof_prefix = str(pathlib.Path(tempfile.mkdtemp()) / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    ddup.config(env="test", service=test_name, version="1.0", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    async def suspended_task(stop: asyncio.Event) -> None:
        await stop.wait()

    async def collecting_task() -> None:
        slow_cyclic_collection()

    async def workload() -> None:
        stop = asyncio.Event()
        suspended = asyncio.create_task(suspended_task(stop), name="suspended-task")
        collecting = asyncio.create_task(collecting_task(), name="collecting-task")
        await collecting
        stop.set()
        await suspended

    with stack.StackCollector():
        asyncio.run(workload())

    ddup.upload()
    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = gc_samples(profile, pprof_utils)
    assert samples
    for sample in samples:
        task_name = pprof_utils.get_label_with_key(profile.string_table, sample, "task name")
        assert task_name is not None
        assert profile.string_table[task_name.str] == "collecting-task"


@pytest.mark.skipif(sys.platform == "win32" or sys.version_info >= (3, 15), reason="fallback callback fork test")
@pytest.mark.subprocess(
    env={
        "DD_PROFILING_STACK_GC_ENABLED": "true",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
        "_DD_PROFILING_STACK_MAX_THREADS": "0",
    },
    err=None,
)
def test_gc_fallback_clears_stale_frame_after_fork_without_duplicate_callback() -> None:
    import gc
    import os
    import pathlib
    import tempfile
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.gc_utils import ddtrace_gc_callbacks
    from tests.profiling.collector.gc_utils import gc_samples

    test_name = "test_gc_fallback_clears_stale_frame_after_fork"
    tmp_path = pathlib.Path(tempfile.mkdtemp())
    pprof_prefix = str(tmp_path / test_name)

    gc.disable()
    ddup.config(env="test", service=test_name, version="1.0", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    collector = stack.StackCollector()
    collector.start()
    callbacks = ddtrace_gc_callbacks(gc)
    assert len(callbacks) == 1
    callbacks[0]("start", {})  # Simulate a collection active at the instant of fork.

    pid = os.fork()
    if pid == 0:
        try:
            assert len(ddtrace_gc_callbacks(gc)) == 1
            child_prefix = str(tmp_path / (test_name + "_child"))
            child_output = child_prefix + "." + str(os.getpid())
            ddup.config(env="test", service=test_name, version="1.0", output_filename=child_prefix)
            ddup.start()
            ddup.upload()

            time.sleep(0.5)
            collector.stop()
            ddup.upload()

            profile = pprof_utils.parse_newest_profile(child_output)
            assert not gc_samples(profile, pprof_utils)
        except Exception:
            import traceback

            traceback.print_exc()
            os._exit(1)
        os._exit(0)

    _, status = os.waitpid(pid, 0)
    callbacks[0]("stop", {})
    collector.stop()
    assert os.waitstatus_to_exitcode(status) == 0
