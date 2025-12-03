import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_sample_count",
        DD_PROFILING_UPLOAD_INTERVAL="1",  # Upload every second
    ),
    err=None,
)
def test_sample_count():
    import asyncio
    import glob
    import json
    import os
    import time
    import uuid

    from ddtrace import ext
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer

    sleep_time = 0.2
    loop_run_time = 2

    async def stuff() -> None:
        start_time = time.time()
        while time.time() < start_time + loop_run_time:
            await asyncio.sleep(sleep_time)

        await asyncio.get_running_loop().run_in_executor(executor=None, func=lambda: time.sleep(1))

    async def hello():
        t1 = asyncio.create_task(stuff(), name="sleep 1")
        t2 = asyncio.create_task(stuff(), name="sleep 2")
        await stuff()
        return (t1, t2)

    resource = str(uuid.uuid4())
    span_type = ext.SpanTypes.WEB

    p = profiler.Profiler(tracer=tracer)
    p.start()
    with tracer.trace("test_asyncio", resource=resource, span_type=span_type):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        maintask = loop.create_task(hello(), name="main")
        loop.run_until_complete(maintask)
    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    files = glob.glob(output_filename + ".*.internal_metadata.json")

    found_at_least_one_with_more_samples_than_sampling_events = False
    for f in files:
        with open(f, "r") as fp:
            internal_metadata = json.load(fp)

            assert internal_metadata is not None
            assert "sample_count" in internal_metadata
            assert internal_metadata["sample_count"] > 0

            assert "sampling_event_count" in internal_metadata
            assert internal_metadata["sampling_event_count"] <= internal_metadata["sample_count"]

            if internal_metadata["sample_count"] > internal_metadata["sampling_event_count"]:
                found_at_least_one_with_more_samples_than_sampling_events = True

    assert found_at_least_one_with_more_samples_than_sampling_events, (
        "Expected at least one file with more samples than sampling events"
    )
