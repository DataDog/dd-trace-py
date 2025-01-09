import os
import threading

import pytest

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling.collector import memalloc
from tests.profiling.collector import pprof_utils


def _allocate_1k():
    return [object() for _ in range(1000)]


_ALLOC_LINE_NUMBER = _allocate_1k.__code__.co_firstlineno + 1


# This test is marked as subprocess as it changes default heap sample size
@pytest.mark.subprocess(
    env=dict(DD_PROFILING_HEAP_SAMPLE_SIZE="1024", DD_PROFILING_OUTPUT_PPROF="/tmp/test_heap_samples_collected")
)
def test_heap_samples_collected():
    import os

    from ddtrace.profiling import Profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling_v2.collector.test_memalloc import _allocate_1k

    # Test for https://github.com/DataDog/dd-trace-py/issues/11069
    pprof_prefix = os.environ["DD_PROFILING_OUTPUT_PPROF"]
    output_filename = pprof_prefix + "." + str(os.getpid())

    p = Profiler()
    p.start()
    x = _allocate_1k()  # noqa: F841
    p.stop()

    profile = pprof_utils.parse_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "heap-space")
    assert len(samples) > 0


def test_memory_collector(tmp_path):
    test_name = "test_memory_collector"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    ddup.config(
        service=test_name,
        version="test",
        env="test",
        output_filename=pprof_prefix,
    )
    ddup.start()

    mc = memalloc.MemoryCollector(None)
    with mc:
        _allocate_1k()
        mc.periodic()

    ddup.upload()

    profile = pprof_utils.parse_profile(output_filename)
    # Gets samples with alloc-space > 0
    samples = pprof_utils.get_samples_with_value_type(profile, "alloc-space")

    assert len(samples) > 0

    alloc_samples_idx = pprof_utils.get_sample_type_index(profile, "alloc-samples")
    for sample in samples:
        # We also want to check 'alloc-samples' is > 0.
        assert sample.value[alloc_samples_idx] > 0

    # We also want to assert that there's a sample that's coming from _allocate_1k()
    # And also assert that it's actually coming from _allocate_1k()
    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            thread_id=threading.main_thread().ident,
            locations=[
                pprof_utils.StackLocation(
                    function_name="_allocate_1k", filename="test_memalloc.py", line_no=_ALLOC_LINE_NUMBER
                )
            ],
        ),
    )


def test_memory_collector_ignore_profiler(tmp_path):
    test_name = "test_memory_collector_ignore_profiler"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    ddup.config(
        service=test_name,
        version="test",
        env="test",
        output_filename=pprof_prefix,
    )
    ddup.start()

    mc = memalloc.MemoryCollector(None, ignore_profiler=True)
    quit_thread = threading.Event()

    with mc:

        def alloc():
            _allocate_1k()
            quit_thread.wait()

        alloc_thread = threading.Thread(name="allocator", target=alloc)
        alloc_thread._ddtrace_profiling_ignore = True
        alloc_thread.start()

        mc.periodic()

    # We need to wait for the data collection to happen so it gets the `_ddtrace_profiling_ignore` Thread attribute from
    # the global thread list.
    quit_thread.set()
    alloc_thread.join()

    ddup.upload()

    try:
        pprof_utils.parse_profile(output_filename)
    except AssertionError as e:
        assert "No samples found" in str(e)
