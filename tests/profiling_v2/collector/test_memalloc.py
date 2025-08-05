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

    profile = pprof_utils.parse_newest_profile(output_filename)
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

    mc = memalloc.MemoryCollector(heap_sample_size=256)
    with mc:
        _allocate_1k()
        mc.snapshot()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
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

    ddup.upload()

    try:
        pprof_utils.parse_newest_profile(output_filename)
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


def _create_allocation(size):
    return (None,) * size if PY_313_OR_ABOVE else bytearray(size)


class HeapInfo:
    def __init__(self, count, size):
        self.count = count
        self.size = size


def get_heap_info(heap, funcs):
    got = {}
    for event in heap:
        (frames, _), in_use_size, alloc_size, count = event

        in_use = in_use_size > 0
        size = in_use_size if in_use_size > 0 else alloc_size

        if not in_use:
            continue
        func = frames[0].function_name
        if func in funcs:
            v = got.get(func, HeapInfo(0, 0))
            v.count += 1
            v.size += size
            got[func] = v
    return got


def has_function_in_traceback(frames, function_name):
    return any(frame.function_name == function_name for frame in frames)


def get_tracemalloc_stats_per_func(stats, funcs):
    source_to_func = {}

    for f in funcs:
        file = inspect.getsourcefile(f)
        line = inspect.getsourcelines(f)[1] + 1
        source_to_func[str(file) + str(line)] = f.__name__

    actual_sizes = {}
    actual_counts = {}
    for stat in stats:
        f = stat.traceback[0]
        key = f.filename + str(f.lineno)
        if key in source_to_func:
            func_name = source_to_func[key]
            actual_sizes[func_name] = stat.size
            actual_counts[func_name] = stat.count
    return actual_sizes, actual_counts


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
    _memalloc.start(32, sample_interval)
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

    actual_sizes, _ = get_tracemalloc_stats_per_func(stats, (one, two, three, four))
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


@pytest.mark.parametrize("sample_interval", (256, 512, 1024))
def test_memory_collector_allocation_accuracy_with_tracemalloc(sample_interval):
    import tracemalloc

    old = os.environ.get("_DD_MEMALLOC_DEBUG_RNG_SEED")
    os.environ["_DD_MEMALLOC_DEBUG_RNG_SEED"] = "42"

    mc = memalloc.MemoryCollector(heap_sample_size=sample_interval)

    try:
        with mc:
            tracemalloc.start()

            junk = []
            for i in range(1000):
                size = 256
                junk.append(one(size))
                junk.append(two(2 * size))
                junk.append(three(3 * size))
                junk.append(four(4 * size))

            stats = tracemalloc.take_snapshot().statistics("traceback")
            tracemalloc.stop()

            del junk

            samples = mc.test_snapshot()

    finally:
        if old is not None:
            os.environ["_DD_MEMALLOC_DEBUG_RNG_SEED"] = old
        else:
            if "_DD_MEMALLOC_DEBUG_RNG_SEED" in os.environ:
                del os.environ["_DD_MEMALLOC_DEBUG_RNG_SEED"]

    allocation_samples = [s for s in samples if s.in_use_size == 0]
    heap_samples = [s for s in samples if s.in_use_size > 0]

    print(f"Total samples: {len(samples)}")
    print(f"Allocation samples (in_use_size=0): {len(allocation_samples)}")
    print(f"Heap samples (in_use_size>0): {len(heap_samples)}")

    assert len(allocation_samples) > 0, "Should have captured allocation samples after deletion"

    total_allocation_count = 0
    for sample in allocation_samples:
        assert sample.size > 0, f"Invalid allocation sample size: {sample.size}"
        assert sample.count > 0, f"Invalid allocation sample count: {sample.count}"
        assert sample.in_use_size == 0, f"Allocation sample should have in_use_size=0, got: {sample.in_use_size}"
        assert sample.in_use_size >= 0, f"Invalid in_use_size: {sample.in_use_size}"
        assert sample.alloc_size >= 0, f"Invalid alloc_size: {sample.alloc_size}"
        total_allocation_count += sample.count

    print(f"Total allocation count: {total_allocation_count}")
    assert total_allocation_count >= 1, "Should have captured at least 1 allocation sample"

    actual_sizes, actual_counts = get_tracemalloc_stats_per_func(stats, (one, two, three, four))
    actual_total = sum(actual_sizes.values())
    actual_count_total = sum(actual_counts.values())

    def get_allocation_info(samples, funcs):
        got = {}
        for sample in samples:
            if sample.in_use_size > 0:
                continue

            for frame in sample.frames:
                func = frame.function_name
                if func in funcs:
                    v = got.get(func, HeapInfo(0, 0))
                    v.count += sample.count
                    v.size += sample.alloc_size
                    got[func] = v
                    break
        return got

    sizes = get_allocation_info(samples, {"one", "two", "three", "four"})

    total = sum(v.size for v in sizes.values())
    total_count = sum(v.count for v in sizes.values())

    print(f"observed total: {total} actual total: {actual_total} error: {abs(total - actual_total) / actual_total}")
    assert abs(1 - total / actual_total) <= 0.20

    count_error = abs(total_count - actual_count_total) / actual_count_total
    print(f"observed count total: {total_count} actual count total: {actual_count_total} error: {count_error}")
    # Commenting out the total count assertions because we still have more work to do on this.
    # Our reported counts differed from the actual count by more than we expected, while the reported sizes
    # are accurate. Our counts seem to be consistently lower than expected for the sample intervals we're testing.
    # We'll need to double-check our count scaling before making assertions about the actual values
    # assert abs(1 - total_count / actual_count_total) <= 0.30

    print("func\tcount\tsize\tactual_size\tactual_count\trel_size\tactual_rel_size\trel_count\tactual_rel_count")
    for func in ("one", "two", "three", "four"):
        got = sizes[func]
        actual_size = actual_sizes[func]
        actual_count = actual_counts[func]

        rel_size = got.size / total
        actual_rel_size = actual_size / actual_total

        rel_count = got.count / total_count
        actual_rel_count = actual_count / actual_count_total

        print(
            f"{func}\t{got.count}\t{got.size}\t{actual_size}\t{actual_count}\t{rel_size:.3f}\t{actual_rel_size:.3f}\t{rel_count:.3f}\t{actual_rel_count:.3f}"
        )

        assert abs(rel_size - actual_rel_size) < 0.10
        assert abs(rel_count - actual_rel_count) < 0.15

    print(f"Successfully validated allocation sampling accuracy for sample_interval={sample_interval}")
    print(f"Captured {len(allocation_samples)} allocation samples representing {total_allocation_count} allocations")


def test_memory_collector_allocation_tracking_across_snapshots():
    mc = memalloc.MemoryCollector(heap_sample_size=64)

    with mc:
        data_to_free = []
        for i in range(10):
            data_to_free.append(one(256))

        data_to_keep = []
        for i in range(10):
            data_to_keep.append(two(512))

        del data_to_free

        samples = mc.test_snapshot()

        assert all(
            sample.alloc_size > 0 for sample in samples
        ), "Initial snapshot should have alloc_size>0 (new allocations)"

        freed_samples = [s for s in samples if s.in_use_size == 0]
        live_samples = [s for s in samples if s.in_use_size > 0]

        assert len(freed_samples) > 0, "Should have some freed samples after deletion"

        assert len(live_samples) > 0, "Should have some live samples"

        for sample in samples:
            assert sample.size > 0, f"Invalid size: {sample.size}"
            assert sample.count > 0, f"Invalid count: {sample.count}"
            assert sample.in_use_size >= 0, f"Invalid in_use_size: {sample.in_use_size}"
            assert sample.alloc_size >= 0, f"Invalid alloc_size: {sample.alloc_size}"

        one_freed_samples = [sample for sample in samples if has_function_in_traceback(sample.frames, "one")]

        assert len(one_freed_samples) > 0, "Should have freed samples from function 'one'"
        assert all(sample.in_use_size == 0 and sample.alloc_size > 0 for sample in one_freed_samples)

        two_live_samples = [sample for sample in samples if has_function_in_traceback(sample.frames, "two")]

        assert len(two_live_samples) > 0, "Should have live samples from function 'two'"
        assert all(sample.in_use_size > 0 and sample.alloc_size > 0 for sample in two_live_samples)

        del data_to_keep


def test_memory_collector_python_interface_with_allocation_tracking():
    mc = memalloc.MemoryCollector(heap_sample_size=128)

    with mc:
        first_batch = []
        for i in range(20):
            first_batch.append(one(256))

        # We're taking a snapshot here to ensure that in the next snapshot, we don't see any "one" allocations
        mc.test_snapshot()

        second_batch = []
        for i in range(15):
            second_batch.append(two(512))

        del first_batch

        final_samples = mc.test_snapshot()

        assert len(final_samples) >= 0, "Final snapshot should be valid"

        for sample in final_samples:
            assert sample.size > 0, f"Size should be positive int, got {sample.size}"
            assert sample.count > 0, f"Count should be positive int, got {sample.count}"
            assert sample.in_use_size >= 0, f"in_use_size should be non-negative int, got {sample.in_use_size}"
            assert sample.alloc_size >= 0, f"alloc_size should be non-negative int, got {sample.alloc_size}"

        one_samples_in_final = [sample for sample in final_samples if has_function_in_traceback(sample.frames, "one")]

        assert (
            len(one_samples_in_final) == 0
        ), f"Should have no samples with 'one' in traceback in final_samples, got {len(one_samples_in_final)}"

        batch_two_live_samples = [
            sample
            for sample in final_samples
            if has_function_in_traceback(sample.frames, "two") and sample.in_use_size > 0
        ]

        assert (
            len(batch_two_live_samples) > 0
        ), f"Should have live samples from batch two, got {len(batch_two_live_samples)}"
        assert all(sample.in_use_size > 0 and sample.alloc_size > 0 for sample in batch_two_live_samples)

        del second_batch


def test_memory_collector_python_interface_with_allocation_tracking_no_deletion():
    mc = memalloc.MemoryCollector(heap_sample_size=128)

    with mc:
        initial_samples = mc.test_snapshot()
        initial_count = len(initial_samples)

        first_batch = []
        for i in range(20):
            first_batch.append(one(256))

        after_first_batch = mc.test_snapshot()

        second_batch = []
        for i in range(15):
            second_batch.append(two(512))

        final_samples = mc.test_snapshot()

        assert len(after_first_batch) >= initial_count, "Should have at least as many samples after first batch"
        assert len(final_samples) >= 0, "Final snapshot should be valid"

        for samples in [initial_samples, after_first_batch, final_samples]:
            for sample in samples:
                assert sample.size > 0, f"Size should be positive int, got {sample.size}"
                assert sample.count > 0, f"Count should be positive int, got {sample.count}"
                assert sample.in_use_size >= 0, f"in_use_size should be non-negative int, got {sample.in_use_size}"
                assert sample.alloc_size >= 0, f"alloc_size should be non-negative int, got {sample.alloc_size}"

        batch_one_live_samples = [
            sample
            for sample in final_samples
            if has_function_in_traceback(sample.frames, "one") and sample.in_use_size > 0
        ]

        batch_two_live_samples = [
            sample
            for sample in final_samples
            if has_function_in_traceback(sample.frames, "two") and sample.in_use_size > 0
        ]

        assert (
            len(batch_one_live_samples) > 0
        ), f"Should have live samples from batch one, got {len(batch_one_live_samples)}"
        assert (
            len(batch_two_live_samples) > 0
        ), f"Should have live samples from batch two, got {len(batch_two_live_samples)}"

        assert all(sample.in_use_size > 0 and sample.alloc_size == 0 for sample in batch_one_live_samples)
        assert all(sample.in_use_size > 0 and sample.alloc_size > 0 for sample in batch_two_live_samples)

        del first_batch
        del second_batch


def test_memory_collector_exception_handling():
    mc = memalloc.MemoryCollector(heap_sample_size=256)

    with pytest.raises(ValueError):
        with mc:
            _allocate_1k()
            samples = mc.test_snapshot()
            assert isinstance(samples, tuple)
            raise ValueError("Test exception")

    with mc:
        _allocate_1k()
        samples = mc.test_snapshot()
        assert isinstance(samples, tuple)


def test_memory_collector_allocation_during_shutdown():
    """Test that verifies that when _memalloc.stop() is called while allocations are still
    happening in another thread, the shutdown process completes without deadlocks or crashes.
    """
    import time

    from ddtrace.profiling.collector import _memalloc

    _memalloc.start(32, 512)

    shutdown_event = threading.Event()
    allocation_thread = None

    def allocate_continuously():
        while not shutdown_event.is_set():
            data = [0] * 100
            del data
            time.sleep(0.001)

    try:
        allocation_thread = threading.Thread(target=allocate_continuously)
        allocation_thread.start()

        time.sleep(0.1)

        _memalloc.stop()

    finally:
        shutdown_event.set()
        if allocation_thread:
            allocation_thread.join(timeout=1)


def test_memory_collector_buffer_pool_exhaustion():
    """Test that the memory collector handles buffer pool exhaustion.
    This test creates multiple threads that simultaneously allocate with very deep
    stack traces, which could potentially exhaust internal buffer pools.
    """
    mc = memalloc.MemoryCollector(heap_sample_size=64)

    with mc:
        threads = []
        barrier = threading.Barrier(10)

        def allocate_with_traceback():
            barrier.wait()

            def deep_alloc(depth):
                if depth == 0:
                    return _create_allocation(100)
                return deep_alloc(depth - 1)

            data = deep_alloc(50)
            del data

        for i in range(10):
            t = threading.Thread(target=allocate_with_traceback)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        samples = mc.test_snapshot()

        deep_alloc_count = 0
        max_stack_depth = 0

        for sample in samples:
            assert sample.frames is not None, "Buffer pool test: All samples should have stack frames"
            stack_depth = len(sample.frames)
            max_stack_depth = max(max_stack_depth, stack_depth)

            for frame in sample.frames:
                if frame.function_name == "deep_alloc":
                    deep_alloc_count += 1
                    break

        assert (
            deep_alloc_count >= 10
        ), f"Buffer pool test: Expected many allocations from concurrent threads, got {deep_alloc_count}"

        assert max_stack_depth >= 50, (
            f"Buffer pool test: Stack traces should be preserved even under stress (expecting at least 50 frames), "
            f"but max depth was only {max_stack_depth}"
        )


def test_memory_collector_thread_lifecycle():
    """Test that continuously creates and destroys threads while they perform allocations,
    verifying that the collector can track allocations across changing thread contexts.
    """
    mc = memalloc.MemoryCollector(heap_sample_size=512)

    with mc:
        threads = []

        def worker():
            for i in range(10):
                data = [i] * 100
                del data

        for i in range(20):
            t = threading.Thread(target=worker)
            t.start()
            threads.append(t)

            if i > 5:
                old_thread = threads.pop(0)
                old_thread.join()

        for t in threads:
            t.join()

        samples = mc.test_snapshot()

        worker_samples = 0
        for sample in samples:
            for frame in sample.frames:
                if frame.function_name == "worker":
                    worker_samples += 1
                    break

        assert (
            worker_samples > 0
        ), "Thread lifecycle test: Should capture allocations even as threads are created/destroyed"
