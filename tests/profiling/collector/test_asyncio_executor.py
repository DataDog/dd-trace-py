import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_executor_origin_task",
    ),
    err=None,
)
# For macOS: err=None ignores expected stderr from tracer failing to connect to agent (not relevant to this test)
def test_asyncio_executor_origin_task_labels() -> None:
    # Work offloaded to a ThreadPoolExecutor by an asyncio task should carry
    # "origin task id"/"origin task name" labels identifying the awaiting task,
    # with "origin task id" equal to that task's own "task id".
    import asyncio
    import os
    import time

    from ddtrace import patch
    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_utils import async_run

    assert stack.is_available, stack.failure_msg

    # The futures integration propagates the originating task across the thread boundary.
    patch(futures=True)

    def slow_sync_function() -> None:
        time.sleep(1)

    async def asynchronous_function() -> None:
        await asyncio.get_running_loop().run_in_executor(executor=None, func=slow_sync_function)

    async def main() -> None:
        await asynchronous_function()

    p = profiler.Profiler()
    p.start()

    async_run(main())

    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)
    string_table = profile.string_table

    # The executor worker samples should be labeled with the originating task.
    origin_task_samples = pprof_utils.get_samples_with_label_key(profile, "origin task id")
    assert len(origin_task_samples) > 0, "expected executor samples labeled with the originating asyncio task"

    # Collect the task ids observed on the event-loop thread (the awaiting task).
    awaiting_task_ids = set()
    for sample in pprof_utils.get_samples_with_label_key(profile, "task id"):
        awaiting_task_ids.add(pprof_utils.get_label_with_key(string_table, sample, "task id").num)
    assert awaiting_task_ids, "expected at least one asyncio task sample on the event-loop thread"

    # Every origin task id seen on a worker thread must match a task id on the loop thread,
    # and the origin task name must be populated.
    for sample in origin_task_samples:
        origin_task_id = pprof_utils.get_label_with_key(string_table, sample, "origin task id").num
        assert origin_task_id in awaiting_task_ids, (
            f"origin task id {origin_task_id} does not match any awaiting task id {awaiting_task_ids}"
        )
        origin_task_name = pprof_utils.get_label_with_key(string_table, sample, "origin task name")
        assert origin_task_name is not None and string_table[origin_task_name.str], "expected origin task name label"

    # The offloaded blocking work itself should be among the origin-task-labeled samples.
    pprof_utils.assert_profile_has_sample(
        profile,
        origin_task_samples,
        expected_sample=pprof_utils.StackEvent(
            locations=[
                pprof_utils.StackLocation(
                    function_name="slow_sync_function",
                    filename="test_asyncio_executor.py",
                    line_no=slow_sync_function.__code__.co_firstlineno + 1,
                ),
            ],
        ),
    )


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_to_thread_origin_task",
    ),
    err=None,
)
# For macOS: err=None ignores expected stderr from tracer failing to connect to agent (not relevant to this test)
def test_asyncio_to_thread_origin_task_labels() -> None:
    # asyncio.to_thread is a higher-level wrapper around run_in_executor; origin
    # task labels should still identify the awaiting asyncio task.
    import asyncio
    import os
    import time

    from ddtrace import patch
    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_utils import async_run

    assert stack.is_available, stack.failure_msg

    patch(futures=True)

    def slow_sync_function() -> None:
        time.sleep(1)

    async def asynchronous_function() -> None:
        await asyncio.to_thread(slow_sync_function)

    async def main() -> None:
        await asynchronous_function()

    p = profiler.Profiler()
    p.start()

    async_run(main())

    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)
    string_table = profile.string_table

    origin_task_samples = pprof_utils.get_samples_with_label_key(profile, "origin task id")
    assert len(origin_task_samples) > 0, "expected to_thread samples labeled with the originating asyncio task"

    awaiting_task_ids = set()
    for sample in pprof_utils.get_samples_with_label_key(profile, "task id"):
        awaiting_task_ids.add(pprof_utils.get_label_with_key(string_table, sample, "task id").num)
    assert awaiting_task_ids, "expected at least one asyncio task sample on the event-loop thread"

    for sample in origin_task_samples:
        origin_task_id = pprof_utils.get_label_with_key(string_table, sample, "origin task id").num
        assert origin_task_id in awaiting_task_ids, (
            f"origin task id {origin_task_id} does not match any awaiting task id {awaiting_task_ids}"
        )
        origin_task_name = pprof_utils.get_label_with_key(string_table, sample, "origin task name")
        assert origin_task_name is not None and string_table[origin_task_name.str], "expected origin task name label"

    pprof_utils.assert_profile_has_sample(
        profile,
        origin_task_samples,
        expected_sample=pprof_utils.StackEvent(
            locations=[
                pprof_utils.StackLocation(
                    function_name="slow_sync_function",
                    filename="test_asyncio_executor.py",
                    line_no=slow_sync_function.__code__.co_firstlineno + 1,
                ),
            ],
        ),
    )


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_anyio_to_thread_span_links",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    err=None,
)
def test_anyio_to_thread_span_links() -> None:
    import os
    import threading
    import time

    from ddtrace import ext
    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    assert stack.is_available, stack.failure_msg

    endpoint = "anyio-worker-endpoint"
    worker_thread_ids = []
    span_ids = []

    def traced_worker_work() -> None:
        worker_thread_ids.append(threading.get_ident())
        time.sleep(0.4)

    def untraced_worker_work() -> None:
        worker_thread_ids.append(threading.get_ident())
        time.sleep(0.4)

    tracer._endpoint_call_counter_span_processor.enable()
    p = profiler.Profiler()
    p.start()

    import anyio

    async def main() -> None:
        with tracer.trace("anyio.request", resource=endpoint, span_type=ext.SpanTypes.WEB) as span:
            span_ids.append(span.span_id)
            await anyio.to_thread.run_sync(traced_worker_work)
        await anyio.to_thread.run_sync(untraced_worker_work)

    anyio.run(main)
    p.stop()

    assert len(worker_thread_ids) == 2
    assert worker_thread_ids[0] == worker_thread_ids[1]

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    wall_samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    traced_samples = pprof_utils.get_samples_with_function(profile, wall_samples, "traced_worker_work")
    untraced_samples = pprof_utils.get_samples_with_function(profile, wall_samples, "untraced_worker_work")

    assert traced_samples
    assert untraced_samples
    assert len(span_ids) == 1
    span_id = pprof_utils.reinterpret_int_as_int64(span_ids[0])
    assert all(pprof_utils.get_str_label(profile, sample, "trace endpoint") == endpoint for sample in traced_samples)
    assert all(pprof_utils.get_num_label(profile, sample, "span id") == span_id for sample in traced_samples)
    assert all(pprof_utils.get_num_label(profile, sample, "local root span id") == span_id for sample in traced_samples)
    assert all(pprof_utils.get_str_label(profile, sample, "trace endpoint") is None for sample in untraced_samples)
    assert all(pprof_utils.get_num_label(profile, sample, "span id") is None for sample in untraced_samples)


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_executor",
    ),
    err=None,
)
# For macOS: err=None ignores expected stderr from tracer failing to connect to agent (not relevant to this test)
def test_asyncio_executor_wall_time() -> None:
    import asyncio
    import os
    from sys import version_info as PYVERSION
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_utils import async_run

    assert stack.is_available, stack.failure_msg

    def slow_sync_function() -> None:
        time.sleep(1)

    async def asynchronous_function() -> None:
        await asyncio.get_running_loop().run_in_executor(executor=None, func=slow_sync_function)

    async def main() -> None:
        await asynchronous_function()

    p = profiler.Profiler()
    p.start()

    async_run(main())

    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())

    profile = pprof_utils.parse_newest_profile(output_filename)

    task_samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(task_samples) > 0

    # Test that we see the asynchronous function stack
    pprof_utils.assert_profile_has_sample(
        profile,
        task_samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            locations=[
                pprof_utils.StackLocation(
                    function_name="asynchronous_function",
                    filename="test_asyncio_executor.py",
                    line_no=asynchronous_function.__code__.co_firstlineno + 1,
                ),
            ],
        ),
    )

    samples = pprof_utils.get_samples_with_label_key(profile, "thread name")
    assert len(samples) > 0

    use_uvloop = os.environ.get("USE_UVLOOP", "0") == "1"

    # uvloop uses ThreadPoolExecutor naming instead of asyncio naming
    if use_uvloop:
        executor_thread_name = "ThreadPoolExecutor-0_0"
    elif PYVERSION >= (3, 9):
        executor_thread_name = "asyncio_0"
    else:
        executor_thread_name = "ThreadPoolExecutor-0_0"

    if PYVERSION >= (3, 11):
        # Thread Pool Executor
        pprof_utils.assert_profile_has_sample(
            profile,
            samples,
            expected_sample=pprof_utils.StackEvent(
                thread_name=executor_thread_name,
                locations=[
                    pprof_utils.StackLocation(
                        function_name="slow_sync_function",
                        filename="test_asyncio_executor.py",
                        line_no=slow_sync_function.__code__.co_firstlineno + 1,
                    ),
                    # pprof_utils.StackLocation(
                    #     function_name="_WorkItem.run",
                    #     filename="",
                    #     line_no=-1,
                    # ),
                    # pprof_utils.StackLocation(
                    #     function_name="_worker",
                    #     filename="",
                    #     line_no=-1,
                    # ),
                    # pprof_utils.StackLocation(
                    #     function_name="Thread.run",
                    #     filename="",
                    #     line_no=-1,
                    # ),
                ],
            ),
        )
    elif PYVERSION >= (3, 9):
        # Thread Pool Executor
        pprof_utils.assert_profile_has_sample(
            profile,
            samples,
            expected_sample=pprof_utils.StackEvent(
                thread_name=executor_thread_name,
                locations=[
                    pprof_utils.StackLocation(
                        function_name="slow_sync_function",
                        filename="test_asyncio_executor.py",
                        line_no=slow_sync_function.__code__.co_firstlineno + 1,
                    ),
                    pprof_utils.StackLocation(
                        function_name="run",
                        filename="",
                        line_no=-1,
                    ),
                    pprof_utils.StackLocation(
                        function_name="_worker",
                        filename="",
                        line_no=-1,
                    ),
                    pprof_utils.StackLocation(
                        function_name="run",
                        filename="",
                        line_no=-1,
                    ),
                ],
            ),
        )
    else:
        # Thread Pool Executor
        pprof_utils.assert_profile_has_sample(
            profile,
            samples,
            expected_sample=pprof_utils.StackEvent(
                thread_name=executor_thread_name,
                locations=[
                    pprof_utils.StackLocation(
                        function_name="slow_sync_function",
                        filename="test_asyncio_executor.py",
                        line_no=slow_sync_function.__code__.co_firstlineno + 1,
                    ),
                    pprof_utils.StackLocation(
                        function_name="run",
                        filename="",
                        line_no=-1,
                    ),
                    pprof_utils.StackLocation(
                        function_name="_worker",
                        filename="",
                        line_no=-1,
                    ),
                    pprof_utils.StackLocation(
                        function_name="run",
                        filename="",
                        line_no=-1,
                    ),
                ],
            ),
        )
