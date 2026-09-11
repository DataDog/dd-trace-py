import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_task_count",
        DD_PROFILING_UPLOAD_INTERVAL="1",
    ),
    err=None,
)
def test_asyncio_task_count_present():
    """asyncio_task_count is present and positive when asyncio tasks are active."""
    import asyncio
    import json
    import os
    import sys
    import time

    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    async def worker():
        await asyncio.sleep(0.5)

    async def main():
        tasks = [asyncio.create_task(worker(), name=f"worker-{i}") for i in range(10)]
        await asyncio.gather(*tasks)

    p = profiler.Profiler(tracer=tracer)
    p.start()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    # Run multiple rounds to ensure tasks are active during profiling
    for _ in range(4):
        loop.run_until_complete(main())
    time.sleep(1)
    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    files = pprof_utils.get_internal_metadata_files(output_filename)
    assert files, "Expected at least one internal_metadata.json file"

    found_initialized = False
    found_loop = False
    found_positive = False
    found_runtime_offsets = False
    for f in files:
        with open(f) as fp:
            metadata = json.load(fp)
        if metadata.get("asyncio_initialized"):
            found_initialized = True
        if metadata.get("asyncio_loop_count", 0) > 0:
            found_loop = True
        if "asyncio_task_count" in metadata:
            assert isinstance(metadata["asyncio_task_count"], int)
            assert metadata["asyncio_task_count"] >= 0
            if metadata["asyncio_task_count"] > 0:
                found_positive = True
        if metadata.get("asyncio_runtime_offsets_discovered"):
            assert metadata["asyncio_interpreter_tasks_head_offset"] > 0
            assert metadata["asyncio_thread_tasks_head_offset"] > 0
            found_runtime_offsets = True

    assert found_initialized, "Expected asyncio initialization to be reported"
    assert found_loop, "Expected at least one tracked asyncio loop"
    assert found_positive, "Expected at least one metadata file with asyncio_task_count > 0"
    if sys.version_info >= (3, 14):
        assert found_runtime_offsets, "Expected Python 3.14 asyncio runtime offsets to be discovered"


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_asyncio_task_count_run_teardown",
    ),
    err=None,
)
def test_asyncio_task_count_survives_run_teardown():
    """asyncio_task_count reflects the peak even after asyncio.run() tears down the loop.

    There may be sampling cycles that observe 0 tasks when other samples in the same profiling interval
    have a higher count. With a plain-assignment setter, the final 0 overwrote the real count captured while the
    loop was live, so the uploaded profile reported asyncio_task_count: 0 when it should have been the peak.
    """
    import asyncio
    import json
    import os
    import time

    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    NUM_WORKERS = 10
    EXPECTED_PEAK = NUM_WORKERS + 1

    async def worker():
        await asyncio.sleep(0.5)

    async def main():
        tasks = [asyncio.create_task(worker(), name=f"worker-{i}") for i in range(NUM_WORKERS)]
        await asyncio.gather(*tasks)

    p = profiler.Profiler(tracer=tracer)
    p.start()

    # use the workload that calls asyncio.run() that clears the event loop on exit.
    for _ in range(4):
        asyncio.run(main())

    # Keep profiling after the loop is gone so several idle cycles (0 tasks) fire.
    time.sleep(1)
    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    files = pprof_utils.get_internal_metadata_files(output_filename)
    assert files, "Expected internal_metadata.json file"

    peak = 0
    for f in files:
        with open(f) as fp:
            metadata = json.load(fp)
        peak = max(peak, metadata.get("asyncio_task_count", 0))

    assert peak == EXPECTED_PEAK, f"Expected asyncio_task_count == {EXPECTED_PEAK}, found {peak}"
