import asyncio
import os
import sys
import time

import pytest

from ddtrace.internal.datadog.profiling import stack_v2
from ddtrace.profiling import _asyncio
from ddtrace.profiling import profiler
from ddtrace.settings.profiling import config
from tests.profiling.collector import _asyncio_compat
from tests.profiling.collector import pprof_utils


@pytest.mark.skipif(sys.version_info < (3, 8), reason="stack v2 is available only on 3.8+ as echion does")
def test_asyncio(monkeypatch, tmp_path):
    pprof_output_prefix = str(tmp_path / "test_asyncio")
    monkeypatch.setattr(config.stack, "v2_enabled", True)
    monkeypatch.setattr(config, "output_pprof", pprof_output_prefix)

    assert stack_v2.is_available, stack_v2.failure_msg

    sleep_time = 0.2
    loop_run_time = 3

    async def stuff() -> None:
        start_time = time.time()
        while time.time() < start_time + loop_run_time:
            await asyncio.sleep(sleep_time)

    async def hello():
        t1 = _asyncio_compat.create_task(stuff(), name="sleep 1")
        t2 = _asyncio_compat.create_task(stuff(), name="sleep 2")
        await stuff()
        return (t1, t2)

    p = profiler.Profiler()
    p.start()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    if _asyncio_compat.PY38_AND_LATER:
        maintask = loop.create_task(hello(), name="main")
    else:
        maintask = loop.create_task(hello())

    t1, t2 = loop.run_until_complete(maintask)
    p.stop()

    t1_name = _asyncio._task_get_name(t1)
    t2_name = _asyncio._task_get_name(t2)

    assert t1_name == "sleep 1"
    assert t2_name == "sleep 2"

    output_filename = pprof_output_prefix + "." + str(os.getpid())

    profile = pprof_utils.parse_profile(output_filename)

    # get samples with task_name
    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    # The next fails if stack_v2 is not properly configured with asyncio task
    # tracking via ddtrace.profiling._asyncio
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            locations=[
                pprof_utils.StackLocation(
                    function_name="hello", filename="test_stack_asyncio.py", line_no=hello.__code__.co_firstlineno + 3
                )
            ],
        ),
    )

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name=t1_name,
            locations=[
                pprof_utils.StackLocation(
                    function_name="stuff", filename="test_stack_asyncio.py", line_no=stuff.__code__.co_firstlineno + 3
                ),
            ],
        ),
    )

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name=t2_name,
            locations=[
                pprof_utils.StackLocation(
                    function_name="stuff", filename="test_stack_asyncio.py", line_no=stuff.__code__.co_firstlineno + 3
                ),
            ],
        ),
    )
