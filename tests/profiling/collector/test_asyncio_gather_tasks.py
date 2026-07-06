import pytest


@pytest.mark.subprocess
def test_asyncio_gather_tasks() -> None:
    import asyncio

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.utils import with_profiling_test_agent

    assert stack.is_available, stack.failure_msg

    async def f1() -> None:
        await f2()

    async def f2() -> None:
        await asyncio.create_task(f3(), name="F3")

    async def f3() -> None:
        await asyncio.gather(*(asyncio.create_task(f4_0(), name="F4_0"), asyncio.create_task(f4_1(), name="F4_1")))

    async def f4_0() -> None:
        await f5()

    async def f4_1() -> None:
        await f5()

    async def f5() -> None:
        await asyncio.sleep(2)

    async def main() -> None:
        await asyncio.create_task(f1(), name="F1")

    with with_profiling_test_agent() as agent_client:
        p = profiler.Profiler()
        p.start()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main())

        p.stop()

        profile = pprof_utils.get_profile_from_agent(agent_client)

        samples = pprof_utils.get_samples_with_label_key(profile, "task name")
        assert len(samples) > 0

        def fn_location(f: str) -> pprof_utils.StackLocation:
            return pprof_utils.StackLocation(
                function_name=f,
                filename="",
                line_no=-1,
            )

        for f, t in (("f4_0", "F4_0"), ("f4_1", "F4_1")):
            pprof_utils.assert_profile_has_sample(
                profile,
                samples,
                expected_sample=pprof_utils.StackEvent(
                    thread_name="MainThread",
                    task_name=t,
                    locations=[
                        fn_location("sleep"),
                        fn_location("f5"),
                        fn_location(f),
                        fn_location("f3"),
                        fn_location("f2"),
                        fn_location("f1"),
                        fn_location("main"),
                    ],
                ),
            )
