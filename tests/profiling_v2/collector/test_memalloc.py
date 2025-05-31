import inspect
import os
import sys
import threading

import pytest

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling.collector import memalloc
from tests.profiling.collector import pprof_utils


PY_313_OR_ABOVE = sys.version_info[:2] >= (3, 13)


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

    ddup.config()
    ddup.start()

    mc = memalloc.MemoryCollector()
    with mc:
        _allocate_1k()
        mc.periodic()

    ddup.upload(output_filename=pprof_prefix)

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

    ddup.config()
    ddup.start()

    mc = memalloc.MemoryCollector(ignore_profiler=True)
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

    ddup.upload(output_filename=pprof_prefix)

    try:
        pprof_utils.parse_profile(output_filename)
    except AssertionError as e:
        assert "No samples found" in str(e)


@pytest.mark.subprocess(
    env=dict(DD_PROFILING_HEAP_SAMPLE_SIZE="8", DD_PROFILING_OUTPUT_PPROF="/tmp/test_heap_profiler_large_heap_overhead")
)
def test_heap_profiler_large_heap_overhead():
    # TODO(nick): this test case used to crash due to integer arithmetic bugs.
    # Now it doesn't crash, but it takes far too long to run to be useful in CI.
    # Un-skip this test if/when we improve the worst-case performance of the
    # heap profiler for large heaps
    from ddtrace.profiling import Profiler
    from tests.profiling_v2.collector.test_memalloc import one

    p = Profiler()
    p.start()

    count = 100_000
    thing_size = 32

    junk = []
    for i in range(count):
        b1 = one(thing_size)
        b2 = one(2 * thing_size)
        b3 = one(3 * thing_size)
        b4 = one(4 * thing_size)
        t = (b1, b2, b3, b4)
        junk.append(t)

    del junk

    p.stop()


# one, two, three, and four exist to give us distinct things
# we can find in the profile without depending on something
# like the line number at which an allocation happens
# Python 3.13 changed bytearray to use an allocation domain that we don't
# currently profile, so we use None instead of bytearray to test.
def one(size):
    return (None,) * size if PY_313_OR_ABOVE else bytearray(size)


def two(size):
    return (None,) * size if PY_313_OR_ABOVE else bytearray(size)


def three(size):
    return (None,) * size if PY_313_OR_ABOVE else bytearray(size)


def four(size):
    return (None,) * size if PY_313_OR_ABOVE else bytearray(size)


class HeapInfo:
    def __init__(self, count, size):
        self.count = count
        self.size = size


def get_heap_info(heap, funcs):
    got = {}
    for (frames, _, _), size in heap:
        func = frames[0].function_name
        if func in funcs:
            v = got.get(func, HeapInfo(0, 0))
            v.count += 1
            v.size += size
            got[func] = v
    return got


def get_tracemalloc_stats_per_func(stats, funcs):
    source_to_func = {}

    for f in funcs:
        file = inspect.getsourcefile(f)
        line = inspect.getsourcelines(f)[1] + 1
        source_to_func[str(file) + str(line)] = f.__name__

    actual_sizes = {}
    for stat in stats:
        f = stat.traceback[0]
        key = f.filename + str(f.lineno)
        if key in source_to_func:
            actual_sizes[source_to_func[key]] = stat.size
    return actual_sizes


# TODO: higher sampling intervals have a lot more variance and are flaky
# but would be nice to test since our default is 1MiB
@pytest.mark.parametrize("sample_interval", (8, 512, 1024))
def test_heap_profiler_sampling_accuracy(sample_interval):
    # tracemalloc lets us get ground truth on how many allocations there were
    import tracemalloc

    # TODO(nick): use Profiler instead of _memalloc
    from ddtrace.profiling.collector import _memalloc

    # We seed the RNG to reduce flakiness. This doesn't actually diminish the
    # quality of the test much. A broken sampling implementation is unlikely to
    # pass for an arbitrary seed.
    old = os.environ.get("_DD_MEMALLOC_DEBUG_RNG_SEED")
    os.environ["_DD_MEMALLOC_DEBUG_RNG_SEED"] = "42"
    _memalloc.start(32, 1000, sample_interval)
    # Put the env var back in the state we found it
    if old is not None:
        os.environ["_DD_MEMALLOC_DEBUG_RNG_SEED"] = old
    else:
        del os.environ["_DD_MEMALLOC_DEBUG_RNG_SEED"]

    tracemalloc.start()

    junk = []
    for i in range(1000):
        size = 256
        junk.append(one(size))
        junk.append(two(2 * size))
        junk.append(three(3 * size))
        junk.append(four(4 * size))

    # TODO(nick): randomly remove things from junk to see if the profile is
    # still accurate

    # Stop tracemalloc before collecting the heap sample, since tracemalloc
    # is _really_ slow when the _memalloc.heap() call does lots of allocs for
    # lower sample intervals (i.e. more sampled allocations)
    stats = tracemalloc.take_snapshot().statistics("traceback")
    tracemalloc.stop()

    heap = _memalloc.heap()
    # Important: stop _memalloc _after_ tracemalloc. Need to remove allocator
    # hooks in LIFO order.
    _memalloc.stop()

    actual_sizes = get_tracemalloc_stats_per_func(stats, (one, two, three, four))
    actual_total = sum(actual_sizes.values())

    del junk

    sizes = get_heap_info(heap, {"one", "two", "three", "four"})

    total = sum(v.size for v in sizes.values())
    print(f"observed total: {total} actual total: {actual_total} error: {abs(total - actual_total) / actual_total}")
    # 20% error in actual size feels pretty generous
    # TODO(nick): justify in terms of variance of sampling?
    assert abs(1 - total / actual_total) <= 0.20

    print("func\tcount\tsize\tactual\trel\tactual\tdiff")
    for func in ("one", "two", "three", "four"):
        got = sizes[func]
        actual_size = actual_sizes[func]

        # Relative portion of the bytes in the profile for this function
        # out of the functions we're interested in
        rel = got.size / total
        actual_rel = actual_size / actual_total

        print(
            f"{func}\t{got.count}\t{got.size}\t{actual_size}\t{rel:.3f}\t{actual_rel:.3f}\t{abs(rel - actual_rel):.3f}"
        )

        # Assert that the reported portion of this function in the profile is
        # pretty close to the actual portion. So, if it's actually ~20% of the
        # profile then we'd accept anything between 10% and 30%, which is
        # probably too generous for low sampling intervals but at least won't be
        # flaky.
        assert abs(rel - actual_rel) < 0.10


@pytest.mark.skip(reason="too slow, indeterministic")
@pytest.mark.subprocess(
    env=dict(
        # Turn off other profilers so that we're just testing memalloc
        DD_PROFILING_STACK_ENABLED="false",
        DD_PROFILING_LOCK_ENABLED="false",
        # Upload a lot, since rotating out memalloc profiler state can race with profiling
        DD_PROFILING_UPLOAD_INTERVAL="1",
    ),
)
def test_memealloc_data_race_regression():
    import gc
    import threading
    import time

    from ddtrace.profiling import Profiler

    gc.enable()
    # This threshold is controls when garbage collection is triggered. The
    # threshold is on the count of live allocations, which is checked when doing
    # a new allocation. This test is ultimately trying to get the allocation of
    # frame objects during the memory profiler's traceback function to trigger
    # garbage collection. We want a lower threshold to improve the odds that
    # this happens.
    gc.set_threshold(100)

    class Thing:
        def __init__(self):
            # Self reference so this gets deallocated in GC vs via refcount
            self.ref = self

        def __del__(self):
            # Force GIL yield,  so if/when memalloc triggers GC, this is
            # deallocated, releasing GIL while memalloc is sampling and allowing
            # something else to run and possibly modify memalloc's internal
            # state concurrently
            time.sleep(0)

    def do_alloc():
        def f():
            return Thing()

        return f

    def lotsa_allocs(ev):
        while not ev.is_set():
            f = do_alloc()
            f()
            time.sleep(0.01)

    p = Profiler()
    p.start()

    threads = []
    ev = threading.Event()
    for i in range(4):
        t = threading.Thread(target=lotsa_allocs, args=(ev,))
        t.start()
        threads.append(t)

    # Arbitrary sleep. This typically crashes in about a minute.
    # But for local development, either let it run way longer or
    # figure out sanitizer instrumentation
    time.sleep(120)

    p.stop()

    ev.set()
    for t in threads:
        t.join()
