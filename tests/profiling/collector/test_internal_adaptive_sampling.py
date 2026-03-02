import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_internal_adaptive_sampling",
        # Upload every second
        DD_PROFILING_UPLOAD_INTERVAL="1",
        # Enable adaptive sampling to test sampling_interval_us field
        _DD_PROFILING_STACK_V2_ADAPTIVE_SAMPLING_ENABLED="1",
    ),
    err=None,
)
def test_internal_adaptive_sampling():
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
    loop_run_time = 4

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
        main_task = loop.create_task(hello(), name="main")
        loop.run_until_complete(main_task)
    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    files = sorted(glob.glob(output_filename + ".*.internal_metadata.json"))

    # With adaptive sampling enabled, the sampling interval can grow up to 1 second
    # (g_max_sampling_period_us). Since the upload interval is also 1 second, the
    # sampling thread may sleep through an entire upload window when the workload is
    # idle, producing files with sample_count == 0. This is expected behavior -- we
    # only require that *some* files have samples, not every individual file.
    found_at_least_one_with_more_samples_than_sampling_events = False
    found_at_least_one_with_sampling_interval = False
    total_sample_count = 0
    for f in files:
        with open(f, "r") as fp:
            internal_metadata = json.load(fp)

            assert internal_metadata is not None
            assert "sample_count" in internal_metadata
            assert "sampling_event_count" in internal_metadata
            assert internal_metadata["sampling_event_count"] <= internal_metadata["sample_count"]

            total_sample_count += internal_metadata["sample_count"]

            # With adaptive sampling enabled, we should have the sampling_interval_us field
            # in files that have samples
            if "sampling_interval_us" in internal_metadata:
                assert internal_metadata["sampling_interval_us"] > 0, (
                    f"Sampling interval should be positive: {internal_metadata['sampling_interval_us']}"
                )
                found_at_least_one_with_sampling_interval = True

            if internal_metadata["sample_count"] > internal_metadata["sampling_event_count"]:
                found_at_least_one_with_more_samples_than_sampling_events = True

        # Early exit if we have already found all the required conditions
        if (
            total_sample_count > 0
            and found_at_least_one_with_more_samples_than_sampling_events
            and found_at_least_one_with_sampling_interval
        ):
            break

    assert total_sample_count > 0, "Expected at least some samples across all files"

    assert found_at_least_one_with_sampling_interval, "Expected at least one file with sampling_interval_us set"

    assert found_at_least_one_with_more_samples_than_sampling_events, (
        "Expected at least one file with more samples than sampling events"
    )
