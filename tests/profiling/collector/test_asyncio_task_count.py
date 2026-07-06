import pytest


@pytest.mark.subprocess(
    # DD_PROFILING_UPLOAD_INTERVAL=1 is required: asyncio_task_count in the stats
    # reflects only the LAST sampling pass before upload. Without a 1s interval the
    # only upload is at p.stop() when all tasks have already finished (count = 0).
    # With 1s interval at least one upload captures tasks that are still active.
    env=dict(DD_PROFILING_UPLOAD_INTERVAL="1"),
)
def test_asyncio_task_count_present():
    """asyncio_task_count is present and positive when asyncio tasks are active."""
    import asyncio
    import time

    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.utils import get_all_metadata_from_agent
    from tests.profiling.utils import with_profiling_test_agent

    async def worker():
        await asyncio.sleep(0.5)

    async def main():
        tasks = [asyncio.create_task(worker(), name=f"worker-{i}") for i in range(10)]
        await asyncio.gather(*tasks)

    with with_profiling_test_agent() as agent_client:
        p = profiler.Profiler(tracer=tracer)
        p.start()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        # Run multiple rounds to ensure tasks are active during profiling
        for _ in range(4):
            loop.run_until_complete(main())
        time.sleep(1)
        p.stop()

        all_metadata = get_all_metadata_from_agent(agent_client, min_count=1)
        assert all_metadata, "Expected at least one internal_metadata entry"

        found_positive = False
        for metadata in all_metadata:
            if "asyncio_task_count" in metadata:
                assert isinstance(metadata["asyncio_task_count"], int)
                assert metadata["asyncio_task_count"] >= 0
                if metadata["asyncio_task_count"] > 0:
                    found_positive = True

        assert found_positive, "Expected at least one metadata file with asyncio_task_count > 0"
