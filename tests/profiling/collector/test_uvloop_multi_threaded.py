import pytest

from tests.profiling.collector import test_utils


uvloop_available = test_utils.uvloop_available()


@pytest.mark.skipif(not uvloop_available, reason="uvloop is not installed in this environment")
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_uvloop_multi_threaded",
    ),
    err=None,
)
# For macOS: err=None ignores expected stderr from tracer failing to connect to agent (not relevant to this test)
def test_uvloop_multi_threaded() -> None:
    import asyncio
    import os
    import threading
    from threading import Event
    import time
    from typing import Optional

    import uvloop

    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_utils import ProfilerContextManager

    sleep_time = 0.2
    loop_run_time = 3

    async def inner() -> None:
        start_time = time.time()
        while time.time() < start_time + loop_run_time:
            await asyncio.sleep(sleep_time)

    async def outer(name: str, event: Optional[Event] = None) -> None:
        if event:
            event.set()

        t1 = asyncio.create_task(inner(), name=f"inner_{name}")
        await t1
        # await asyncio.wait(fs=(t1,), return_when=asyncio.ALL_COMPLETED)

    with ProfilerContextManager():
        event = Event()

        def threaded_func() -> None:
            # uvloop.run only affects the current Thread
            # event is passed to the coroutine so we're sure it's set AFTER the loop has been
            # started.
            uvloop.run(outer("uvloop", event))

        thread = threading.Thread(target=threaded_func, name="UvloopThread")
        thread.start()

        # Wait for the thread to start, then give it some time to start uvloop
        event.wait()
        time.sleep(0.05)

        # Now run our coroutine with "pure asyncio"
        asyncio.run(outer("pure_asyncio"))

        # Wait for the thread to finish
        thread.join()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)

    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="UvloopThread",
            task_name="inner_uvloop",
            locations=[
                pprof_utils.StackLocation(
                    function_name="sleep",
                    filename="",
                    line_no=-1,
                ),
                pprof_utils.StackLocation(
                    function_name="inner",
                    filename="test_uvloop_multi_threaded.py",
                    line_no=-1,
                ),
                pprof_utils.StackLocation(
                    function_name="outer",
                    filename="test_uvloop_multi_threaded.py",
                    line_no=outer.__code__.co_firstlineno + 5,
                ),
                pprof_utils.StackLocation(
                    # Thread Bootstrap
                    function_name="",
                    filename="threading.py",
                    line_no=-1,
                ),
            ],
        ),
        print_samples_on_failure=True,
    )

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name="inner_pure_asyncio",
            locations=[
                pprof_utils.StackLocation(
                    function_name="sleep",
                    filename="",
                    line_no=-1,
                ),
                pprof_utils.StackLocation(
                    function_name="inner",
                    filename="test_uvloop_multi_threaded.py",
                    line_no=inner.__code__.co_firstlineno + 3,
                ),
                pprof_utils.StackLocation(
                    function_name="outer",
                    filename="test_uvloop_multi_threaded.py",
                    line_no=outer.__code__.co_firstlineno + 5,
                ),
            ],
        ),
        print_samples_on_failure=True,
    )
