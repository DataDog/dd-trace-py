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
    import glob
    import json
    import os
    import time

    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer

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
    files = sorted(glob.glob(output_filename + ".*.internal_metadata.json"))
    assert files, "Expected at least one internal_metadata.json file"

    found_positive = False
    for f in files:
        with open(f) as fp:
            metadata = json.load(fp)
        if "asyncio_task_count" in metadata:
            assert isinstance(metadata["asyncio_task_count"], int)
            assert metadata["asyncio_task_count"] >= 0
            if metadata["asyncio_task_count"] > 0:
                found_positive = True

    assert found_positive, "Expected at least one metadata file with asyncio_task_count > 0"
