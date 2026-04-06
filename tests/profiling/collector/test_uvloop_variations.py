import pytest

from tests.profiling.collector import test_utils


uvloop_available = test_utils.uvloop_available()


@pytest.mark.skipif(not uvloop_available, reason="uvloop is not installed in this environment")
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_uvloop_variations",
    ),
    err=None,
)
# For macOS: err=None ignores expected stderr from tracer failing to connect to agent (not relevant to this test)
def test_uvloop_variations_install_and_run() -> None:
    """Test that we properly profile Tasks when running uvloop with uvloop.install, then asyncio.run."""
    import asyncio
    import os
    import time

    import uvloop

    from ddtrace.internal.datadog.profiling import stack
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_utils import ProfilerContextManager

    assert stack.is_available, stack.failure_msg

    sleep_time = 0.2
    loop_run_time = 3

    async def inner1() -> None:
        start_time = time.time()
        while time.time() < start_time + loop_run_time:
            await asyncio.sleep(sleep_time)

    async def inner2() -> None:
        start_time = time.time()
        while time.time() < start_time + loop_run_time:
            await asyncio.sleep(sleep_time)

    async def outer() -> None:
        t1 = asyncio.create_task(inner1(), name="inner 1")
        t2 = asyncio.create_task(inner2(), name="inner 2")
        await asyncio.wait(fs=(t1, t2), return_when=asyncio.ALL_COMPLETED)

    with ProfilerContextManager():
        uvloop.install()
        asyncio.run(outer())

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)

    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name="inner 1",
            locations=[
                pprof_utils.StackLocation(
                    function_name="inner1",
                    filename="test_uvloop_variations.py",
                    line_no=inner1.__code__.co_firstlineno + 3,
                ),
                pprof_utils.StackLocation(
                    function_name="outer",
                    filename="test_uvloop_variations.py",
                    line_no=outer.__code__.co_firstlineno + 3,
                ),
                pprof_utils.StackLocation(
                    function_name="<module>",
                    filename="test_uvloop_variations.py",
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
            task_name="inner 2",
            locations=[
                pprof_utils.StackLocation(
                    function_name="inner2",
                    filename="test_uvloop_variations.py",
                    line_no=inner2.__code__.co_firstlineno + 3,
                ),
                pprof_utils.StackLocation(
                    function_name="outer",
                    filename="test_uvloop_variations.py",
                    line_no=outer.__code__.co_firstlineno + 3,
                ),
                pprof_utils.StackLocation(
                    function_name="<module>",
                    filename="test_uvloop_variations.py",
                    line_no=-1,
                ),
            ],
        ),
        print_samples_on_failure=True,
    )

    pprof_utils.assert_profile_does_not_have_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            locations=[
                pprof_utils.StackLocation(
                    # base_events.py is only present when using asyncio, we should not see it here.
                    function_name="run_forever",
                    filename="base_events.py",
                    line_no=-1,
                ),
            ],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.skipif(not uvloop_available, reason="uvloop is not installed in this environment")
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_uvloop_variations_uvloop_run",
    ),
    err=None,
)
# For macOS: err=None ignores expected stderr from tracer failing to connect to agent (not relevant to this test)
def test_uvloop_variations_uvloop_run() -> None:
    """Test that we properly profile Tasks when running uvloop with uvloop.run."""
    import asyncio
    import os
    import time

    import uvloop

    from ddtrace.internal.datadog.profiling import stack
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_utils import ProfilerContextManager

    assert stack.is_available, stack.failure_msg

    sleep_time = 0.2
    loop_run_time = 3

    async def inner1() -> None:
        start_time = time.time()
        while time.time() < start_time + loop_run_time:
            await asyncio.sleep(sleep_time)

    async def inner2() -> None:
        start_time = time.time()
        while time.time() < start_time + loop_run_time:
            await asyncio.sleep(sleep_time)

    async def outer() -> None:
        t1 = asyncio.create_task(inner1(), name="inner 1")
        t2 = asyncio.create_task(inner2(), name="inner 2")
        await asyncio.wait(fs=(t1, t2), return_when=asyncio.ALL_COMPLETED)

    with ProfilerContextManager():
        uvloop.run(outer())

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)

    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name="inner 1",
            locations=[
                pprof_utils.StackLocation(
                    function_name="inner1",
                    filename="test_uvloop_variations.py",
                    line_no=inner1.__code__.co_firstlineno + 3,
                ),
                pprof_utils.StackLocation(
                    function_name="outer",
                    filename="test_uvloop_variations.py",
                    line_no=outer.__code__.co_firstlineno + 3,
                ),
                pprof_utils.StackLocation(
                    function_name="<module>",
                    filename="test_uvloop_variations.py",
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
            task_name="inner 2",
            locations=[
                pprof_utils.StackLocation(
                    function_name="inner2",
                    filename="test_uvloop_variations.py",
                    line_no=inner2.__code__.co_firstlineno + 3,
                ),
                pprof_utils.StackLocation(
                    function_name="outer",
                    filename="test_uvloop_variations.py",
                    line_no=outer.__code__.co_firstlineno + 3,
                ),
                pprof_utils.StackLocation(
                    function_name="<module>",
                    filename="test_uvloop_variations.py",
                    line_no=-1,
                ),
            ],
        ),
        print_samples_on_failure=True,
    )

    pprof_utils.assert_profile_does_not_have_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            locations=[
                pprof_utils.StackLocation(
                    # base_events.py is only present when using asyncio, we should not see it here.
                    function_name="run_forever",
                    filename="base_events.py",
                    line_no=-1,
                ),
            ],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.skipif(not uvloop_available, reason="uvloop is not installed in this environment")
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_uvloop_variations_import_uvloop_dont_use_it",
    ),
    err=None,
)
# For macOS: err=None ignores expected stderr from tracer failing to connect to agent (not relevant to this test)
def test_uvloop_variations_import_uvloop_dont_use_it() -> None:
    """Test that we properly profile Tasks when importing uvloop but not using it."""
    import asyncio
    import os
    import time

    import uvloop  # noqa: F401

    from ddtrace.internal.datadog.profiling import stack
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_utils import ProfilerContextManager

    assert stack.is_available, stack.failure_msg

    sleep_time = 0.2
    loop_run_time = 3

    async def inner1() -> None:
        start_time = time.time()
        while time.time() < start_time + loop_run_time:
            await asyncio.sleep(sleep_time)

    async def inner2() -> None:
        start_time = time.time()
        while time.time() < start_time + loop_run_time:
            await asyncio.sleep(sleep_time)

    async def outer() -> None:
        t1 = asyncio.create_task(inner1(), name="inner 1")
        t2 = asyncio.create_task(inner2(), name="inner 2")
        await asyncio.wait(fs=(t1, t2), return_when=asyncio.ALL_COMPLETED)

    with ProfilerContextManager():
        # uvloop is not installed nor used!
        asyncio.run(outer())

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)

    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name="inner 1",
            locations=[
                pprof_utils.StackLocation(
                    function_name="sleep",
                    filename="tasks.py",
                    line_no=-1,
                ),
                pprof_utils.StackLocation(
                    function_name="inner1",
                    filename="test_uvloop_variations.py",
                    line_no=inner1.__code__.co_firstlineno + 3,
                ),
                pprof_utils.StackLocation(
                    function_name="outer",
                    filename="test_uvloop_variations.py",
                    line_no=outer.__code__.co_firstlineno + 3,
                ),
                pprof_utils.StackLocation(
                    # This Frame is only present when using asyncio
                    function_name="run_forever",
                    filename="base_events.py",
                    line_no=-1,
                ),
                pprof_utils.StackLocation(
                    function_name="<module>",
                    filename="test_uvloop_variations.py",
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
            task_name="inner 2",
            locations=[
                pprof_utils.StackLocation(
                    function_name="sleep",
                    filename="tasks.py",
                    line_no=-1,
                ),
                pprof_utils.StackLocation(
                    function_name="inner2",
                    filename="test_uvloop_variations.py",
                    line_no=inner2.__code__.co_firstlineno + 3,
                ),
                pprof_utils.StackLocation(
                    function_name="outer",
                    filename="test_uvloop_variations.py",
                    line_no=outer.__code__.co_firstlineno + 3,
                ),
                pprof_utils.StackLocation(
                    # This Frame is only present when using asyncio
                    function_name="run_forever",
                    filename="base_events.py",
                    line_no=-1,
                ),
                pprof_utils.StackLocation(
                    function_name="<module>",
                    filename="test_uvloop_variations.py",
                    line_no=-1,
                ),
            ],
        ),
        print_samples_on_failure=True,
    )
