import asyncio
import glob
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
def test_asyncio(monkeypatch):
    pprof_output_prefix = "/tmp/test_asyncio"
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

    # We'd like to check whether there exist samples with
    # 1. task name label "main"
    #   - function name label "hello"
    #   - and line number is between
    # 2. task name label t1_name or t2_name
    #  - function name label "stuff"
    # And they all have thread name "MainThread"

    checked_main = False
    checked_t1 = False
    checked_t2 = False

    for sample in samples:
        task_name_label = pprof_utils.get_label_with_key(profile.string_table, sample, "task name")
        task_name = profile.string_table[task_name_label.str]

        thread_name_label = pprof_utils.get_label_with_key(profile.string_table, sample, "thread name")
        thread_name = profile.string_table[thread_name_label.str]

        location_id = sample.location_id[0]
        location = pprof_utils.get_location_with_id(profile, location_id)
        line = location.line[0]
        function = pprof_utils.get_function_with_id(profile, line.function_id)
        function_name = profile.string_table[function.name]

        if task_name == "main":
            assert thread_name == "MainThread"
            assert function_name == "hello"
            checked_main = True
        elif task_name == t1_name or task_name == t2_name:
            assert thread_name == "MainThread"
            assert function_name == "stuff"
            if task_name == t1_name:
                checked_t1 = True
            if task_name == t2_name:
                checked_t2 = True

    assert checked_main
    assert checked_t1
    assert checked_t2

    # cleanup output file
    for f in glob.glob(pprof_output_prefix + ".*"):
        try:
            os.remove(f)
        except Exception as e:
            print("Error removing file: {}".format(e))
        pass
