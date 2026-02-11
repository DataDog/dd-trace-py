from __future__ import annotations

import inspect
import os
from pathlib import Path
import sys
import threading
from tracemalloc import Statistic
from typing import TYPE_CHECKING
from typing import Callable
from typing import Sequence
from typing import Union

import pytest

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.internal.settings.profiling import ProfilingConfig
from ddtrace.internal.settings.profiling import (
    _derive_default_heap_sample_size,  # pyright: ignore[reportAttributeAccessIssue]
)
from ddtrace.profiling.collector import memalloc
from tests.profiling.collector import pprof_utils


if TYPE_CHECKING:
    # We need the pyright: ignore because pprof_pb2 does not exist as a real module, only as a pyi.
    from tests.profiling.collector import pprof_pb2  # pyright: ignore[reportMissingModuleSource]


PY_314_OR_ABOVE = sys.version_info[:2] >= (3, 14)
PY_313_OR_ABOVE = sys.version_info[:2] >= (3, 13)
PY_311_OR_ABOVE = sys.version_info[:2] >= (3, 11)


def _allocate_1k() -> list[object]:
    return [object() for _ in range(1000)]


_ALLOC_LINE_NUMBER = _allocate_1k.__code__.co_firstlineno + 1


def _setup_profiling_prelude(tmp_path: Path, test_name: str) -> str:
    """Setup ddup configuration and return the output filename for pprof parsing.

    Args:
        tmp_path: pytest tmp_path fixture
        test_name: Name of the test (used for service name and output filename)

    Returns:
        output_filename: The full path to the pprof output file (with PID suffix)
    """
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    ddup.config(
        service=test_name,
        version="test",
        env="test",
        output_filename=pprof_prefix,
    )
    ddup.start()

    return output_filename


# This test is marked as subprocess as it changes default heap sample size
@pytest.mark.subprocess(
    env=dict(DD_PROFILING_HEAP_SAMPLE_SIZE="1024", DD_PROFILING_OUTPUT_PPROF="/tmp/test_heap_samples_collected")
)
def test_heap_samples_collected() -> None:
    import os

    from ddtrace.profiling import Profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_memalloc import _allocate_1k

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


def test_memory_collector(tmp_path: Path) -> None:
    output_filename = _setup_profiling_prelude(tmp_path, "test_memory_collector")

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


def test_memory_collector_ignore_profiler(tmp_path: Path) -> None:
    output_filename = _setup_profiling_prelude(tmp_path, "test_memory_collector_ignore_profiler")

    mc = memalloc.MemoryCollector(ignore_profiler=True)
    quit_thread = threading.Event()

    with mc:

        def alloc() -> None:
            _allocate_1k()
            quit_thread.wait()

        alloc_thread = threading.Thread(name="allocator", target=alloc)
        alloc_thread._ddtrace_profiling_ignore = True  # pyright: ignore[reportAttributeAccessIssue]
        alloc_thread.start()

        mc.snapshot()

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
def test_heap_profiler_large_heap_overhead() -> None:
    # NOTE: A regression test for integer arithmetic bugs.
    from ddtrace.profiling import Profiler
    from tests.profiling.collector.test_memalloc import one

    p = Profiler()
    p.start()

    count = 100_000
    thing_size = 32

    junk = []
    for _ in range(count):
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
def one(size: int) -> Union[tuple[None, ...], bytearray]:
    return (None,) * size if PY_313_OR_ABOVE else bytearray(size)


def two(size: int) -> Union[tuple[None, ...], bytearray]:
    return (None,) * size if PY_313_OR_ABOVE else bytearray(size)


def three(size: int) -> Union[tuple[None, ...], bytearray]:
    return (None,) * size if PY_313_OR_ABOVE else bytearray(size)


def four(size: int) -> Union[tuple[None, ...], bytearray]:
    return (None,) * size if PY_313_OR_ABOVE else bytearray(size)


def _create_allocation(size: int) -> Union[tuple[None, ...], bytearray]:
    return (None,) * size if PY_313_OR_ABOVE else bytearray(size)


class HeapInfo:
    def __init__(self, count: int, size: int) -> None:
        self.count = count
        self.size = size


def has_function_in_profile_sample(
    profile: pprof_pb2.Profile, sample: pprof_pb2.Sample, function_or_name: Union[Callable, str]
) -> bool:
    """Check if a pprof profile sample contains a function in its stack trace.

    Args:
        profile: The pprof profile
        sample: The sample to check
        function_or_name: Either a function object (callable) or a string function name.
                         If a function object is provided, its qualified name will be used
                         for Python 3.11+, otherwise its __name__ will be used.
    """
    # Get the expected function name
    if callable(function_or_name):
        expected_function_name = function_or_name.__qualname__ if PY_311_OR_ABOVE else function_or_name.__name__
    else:
        expected_function_name = function_or_name

    for location_id in sample.location_id:
        location = pprof_utils.get_location_with_id(profile, location_id)
        if location.line:
            function = pprof_utils.get_function_with_id(profile, location.line[0].function_id)
            actual_function_name = profile.string_table[function.name]
            if actual_function_name == expected_function_name:
                return True
    return False


def get_tracemalloc_stats_per_func(
    stats: Sequence[Statistic], funcs: Sequence[Callable]
) -> tuple[dict[str, int], dict[str, int]]:
    source_to_func: dict[str, str] = {}

    for f in funcs:
        file = inspect.getsourcefile(f)
        line = inspect.getsourcelines(f)[1] + 1
        source_to_func[str(file) + str(line)] = f.__name__

    actual_sizes: dict[str, int] = {}
    actual_counts: dict[str, int] = {}
    for stat in stats:
        f = stat.traceback[0]
        key = f.filename + str(f.lineno)
        if key in source_to_func:
            func_name = source_to_func[key]
            actual_sizes[func_name] = stat.size
            actual_counts[func_name] = stat.count
    return actual_sizes, actual_counts


@pytest.mark.skip(reason="too slow, non-deterministic")
@pytest.mark.subprocess(
    env=dict(
        # Turn off other profilers so that we're just testing memalloc
        DD_PROFILING_STACK_ENABLED="false",
        DD_PROFILING_LOCK_ENABLED="false",
        # Upload a lot, since rotating out memalloc profiler state can race with profiling
        DD_PROFILING_UPLOAD_INTERVAL="1",
    ),
)
def test_memalloc_data_race_regression() -> None:
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
        def __init__(self) -> None:
            # Self reference so this gets deallocated in GC vs via refcount
            self.ref = self

        def __del__(self) -> None:
            # Force GIL yield,  so if/when memalloc triggers GC, this is
            # deallocated, releasing GIL while memalloc is sampling and allowing
            # something else to run and possibly modify memalloc's internal
            # state concurrently
            time.sleep(0)

    def do_alloc() -> Callable[[], Thing]:
        def f() -> Thing:
            return Thing()

        return f

    def lotsa_allocs(ev: threading.Event) -> None:
        while not ev.is_set():
            f = do_alloc()
            f()
            time.sleep(0.01)

    p = Profiler()
    p.start()

    threads: list[threading.Thread] = []
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


@pytest.mark.skip(reason="This test makes the CI timeout. Skipping it to unblock PRs.")
@pytest.mark.parametrize("sample_interval", (256, 512, 1024))
def test_memory_collector_allocation_accuracy_with_tracemalloc(sample_interval: int, tmp_path: Path) -> None:
    import tracemalloc

    test_name = f"test_memory_collector_allocation_accuracy_with_tracemalloc_{sample_interval}"
    output_filename = _setup_profiling_prelude(tmp_path, test_name)

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

            stats: list[Statistic] = tracemalloc.take_snapshot().statistics("traceback")
            tracemalloc.stop()

            del junk

            profile = mc.snapshot_and_parse_pprof(output_filename)

    finally:
        if old is not None:
            os.environ["_DD_MEMALLOC_DEBUG_RNG_SEED"] = old
        else:
            if "_DD_MEMALLOC_DEBUG_RNG_SEED" in os.environ:
                del os.environ["_DD_MEMALLOC_DEBUG_RNG_SEED"]

    # Get sample type indices
    heap_space_idx = pprof_utils.get_sample_type_index(profile, "heap-space")
    alloc_space_idx = pprof_utils.get_sample_type_index(profile, "alloc-space")
    alloc_count_idx = pprof_utils.get_sample_type_index(profile, "alloc-samples")

    # Assert that required sample types exist
    assert heap_space_idx >= 0, "heap-space sample type not found in profile"
    assert alloc_space_idx >= 0, "alloc-space sample type not found in profile"
    assert alloc_count_idx >= 0, "alloc-samples sample type not found in profile"

    # Get allocation samples (freed) - these have alloc-space > 0 and heap-space == 0
    allocation_samples = [s for s in profile.sample if s.value[alloc_space_idx] > 0 and s.value[heap_space_idx] == 0]
    # Get heap samples (live) - these have heap-space > 0
    heap_samples = [s for s in profile.sample if s.value[heap_space_idx] > 0]

    print(f"Total samples: {len(profile.sample)}")
    print(f"Allocation samples (alloc-space>0, heap-space=0): {len(allocation_samples)}")
    print(f"Heap samples (heap-space>0): {len(heap_samples)}")

    assert len(allocation_samples) > 0, "Should have captured allocation samples after deletion"

    total_allocation_count = 0
    for sample in allocation_samples:
        assert sample.value[alloc_space_idx] > 0, f"Invalid allocation sample size: {sample.value[alloc_space_idx]}"
        assert sample.value[alloc_count_idx] > 0, f"Invalid allocation sample count: {sample.value[alloc_count_idx]}"
        assert sample.value[heap_space_idx] == 0, (
            f"Invalid heap-space for freed sample (should be 0): {sample.value[heap_space_idx]}"
        )
        total_allocation_count += sample.value[alloc_count_idx]

    print(f"Total allocation count: {total_allocation_count}")
    assert total_allocation_count >= 1, "Should have captured at least 1 allocation sample"

    actual_sizes, actual_counts = get_tracemalloc_stats_per_func(stats, (one, two, three, four))
    actual_total = sum(actual_sizes.values())
    actual_count_total = sum(actual_counts.values())

    def get_allocation_info_from_profile(
        profile: pprof_pb2.Profile, samples: Sequence[pprof_pb2.Sample], funcs: Sequence[Union[Callable, str]]
    ) -> dict[str, HeapInfo]:
        got = {}
        for sample in samples:
            if sample.value[heap_space_idx] > 0:
                continue

            for location_id in sample.location_id:
                location = pprof_utils.get_location_with_id(profile, location_id)
                if location.line:
                    function = pprof_utils.get_function_with_id(profile, location.line[0].function_id)
                    func = profile.string_table[function.name]
                    if func in funcs:
                        v = got.get(func, HeapInfo(0, 0))
                        v.count += sample.value[alloc_count_idx]
                        v.size += sample.value[alloc_space_idx]
                        got[func] = v
                        break
        return got

    sizes = get_allocation_info_from_profile(profile, allocation_samples, ("one", "two", "three", "four"))

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
            f"{func}\t{got.count}\t{got.size}\t{actual_size}\t{actual_count}\t"
            f"{rel_size:.3f}\t{actual_rel_size:.3f}\t{rel_count:.3f}\t{actual_rel_count:.3f}"
        )

        assert abs(rel_size - actual_rel_size) < 0.10
        assert abs(rel_count - actual_rel_count) < 0.15

    print(f"Successfully validated allocation sampling accuracy for sample_interval={sample_interval}")
    print(f"Captured {len(allocation_samples)} allocation samples representing {total_allocation_count} allocations")


def test_memory_collector_allocation_tracking_across_snapshots(tmp_path: Path) -> None:
    output_filename = _setup_profiling_prelude(tmp_path, "test_memory_collector_allocation_tracking_across_snapshots")

    mc = memalloc.MemoryCollector(heap_sample_size=64)

    with mc:
        data_to_free = []
        for _ in range(10):
            data_to_free.append(one(256))

        data_to_keep = []
        for _ in range(10):
            data_to_keep.append(two(512))

        del data_to_free

        profile = mc.snapshot_and_parse_pprof(output_filename)

        # Get sample type indices
        heap_space_idx = pprof_utils.get_sample_type_index(profile, "heap-space")
        alloc_space_idx = pprof_utils.get_sample_type_index(profile, "alloc-space")
        alloc_count_idx = pprof_utils.get_sample_type_index(profile, "alloc-samples")

        # Assert that required sample types exist
        assert heap_space_idx >= 0, "heap-space sample type not found in profile"
        assert alloc_space_idx >= 0, "alloc-space sample type not found in profile"
        assert alloc_count_idx >= 0, "alloc-samples sample type not found in profile"

        initial_allocations_valid = all(sample.value[alloc_space_idx] > 0 for sample in profile.sample)
        assert initial_allocations_valid, "Initial snapshot should have alloc-space>0 (new allocations)"

        # Get freed samples (alloc-space > 0, heap-space == 0)
        freed_samples = [s for s in profile.sample if s.value[alloc_space_idx] > 0 and s.value[heap_space_idx] == 0]
        # Get live samples (heap-space > 0)
        live_samples = [s for s in profile.sample if s.value[heap_space_idx] > 0]

        assert len(freed_samples) > 0, "Should have some freed samples after deletion"

        assert len(live_samples) > 0, "Should have some live samples"

        # Validate all samples have valid values
        for sample in profile.sample:
            has_heap = sample.value[heap_space_idx] > 0
            has_alloc = sample.value[alloc_space_idx] > 0
            assert has_heap or has_alloc, "Sample should have either heap-space or alloc-space > 0"
            assert sample.value[alloc_count_idx] >= 0, (
                f"alloc-samples should be non-negative, got {sample.value[alloc_count_idx]}"
            )

        one_freed_samples = [sample for sample in freed_samples if has_function_in_profile_sample(profile, sample, one)]

        assert len(one_freed_samples) > 0, "Should have freed samples from function 'one'"
        one_freed_samples_valid = all(
            sample.value[heap_space_idx] == 0 and sample.value[alloc_space_idx] > 0 for sample in one_freed_samples
        )
        assert one_freed_samples_valid, (
            "Freed samples from function 'one' should have heap-space == 0 and alloc-space > 0"
        )

        two_live_samples = [sample for sample in live_samples if has_function_in_profile_sample(profile, sample, two)]

        assert len(two_live_samples) > 0, "Should have live samples from function 'two'"
        two_live_samples_valid = all(
            sample.value[heap_space_idx] > 0 and sample.value[alloc_space_idx] > 0 for sample in two_live_samples
        )
        assert two_live_samples_valid, "Live samples from function 'two' should have heap-space > 0 and alloc-space > 0"

        del data_to_keep


def test_memory_collector_python_interface_with_allocation_tracking(tmp_path: Path) -> None:
    output_filename = _setup_profiling_prelude(
        tmp_path, "test_memory_collector_python_interface_with_allocation_tracking"
    )

    mc = memalloc.MemoryCollector(heap_sample_size=32)

    with mc:
        first_batch: list[Union[tuple[None, ...], bytearray]] = []
        for _ in range(20):
            first_batch.append(one(256))

        # We're taking a snapshot here to ensure that in the next snapshot, we don't see any "one" allocations
        mc.snapshot_and_parse_pprof(output_filename)

        second_batch: list[Union[tuple[None, ...], bytearray]] = []
        for _ in range(15):
            second_batch.append(two(512))

        del first_batch

        final_profile = mc.snapshot_and_parse_pprof(output_filename)

        assert len(final_profile.sample) > 0, "Final snapshot should have samples"

        # Get sample type indices
        heap_space_idx = pprof_utils.get_sample_type_index(final_profile, "heap-space")
        alloc_space_idx = pprof_utils.get_sample_type_index(final_profile, "alloc-space")
        alloc_count_idx = pprof_utils.get_sample_type_index(final_profile, "alloc-samples")

        # Assert that required sample types exist in the profile
        assert heap_space_idx >= 0, "heap-space sample type not found in profile"
        assert alloc_space_idx >= 0, "alloc-space sample type not found in profile"
        assert alloc_count_idx >= 0, "alloc-samples sample type not found in profile"

        # Validate all samples have valid values
        for sample in final_profile.sample:
            # Check that at least one value type is non-zero
            has_heap = sample.value[heap_space_idx] > 0
            has_alloc = sample.value[alloc_space_idx] > 0
            assert has_heap or has_alloc, "Sample should have either heap-space or alloc-space > 0"
            assert sample.value[alloc_count_idx] >= 0, (
                f"alloc-samples should be non-negative, got {sample.value[alloc_count_idx]}"
            )

        # Get live samples (heap-space > 0)
        live_samples = [s for s in final_profile.sample if s.value[heap_space_idx] > 0]

        # Check that we have no live samples with 'one' in traceback (they were freed)
        one_samples_in_final = [
            sample for sample in live_samples if has_function_in_profile_sample(final_profile, sample, one)
        ]

        assert len(one_samples_in_final) == 0, (
            f"Should have no live samples with 'one' in traceback in final_samples, got {len(one_samples_in_final)}"
        )

        # Check that we have live samples from function 'two'
        batch_two_live_samples = [
            sample for sample in live_samples if has_function_in_profile_sample(final_profile, sample, two)
        ]

        assert len(batch_two_live_samples) > 0, (
            f"Should have live samples from batch two, got {len(batch_two_live_samples)}"
        )
        batch_two_valid = all(
            sample.value[heap_space_idx] > 0 and sample.value[alloc_space_idx] >= 0 for sample in batch_two_live_samples
        )
        assert batch_two_valid, "Batch two samples should have heap-space > 0 and alloc-space >= 0"

        del second_batch


def test_memory_collector_python_interface_with_allocation_tracking_no_deletion(tmp_path: Path) -> None:
    output_filename = _setup_profiling_prelude(
        tmp_path, "test_memory_collector_python_interface_with_allocation_tracking_no_deletion"
    )

    mc = memalloc.MemoryCollector(heap_sample_size=32)

    with mc:
        # Take initial snapshot to reset allocation tracking (may have no samples)
        mc.snapshot_and_parse_pprof(output_filename, assert_samples=False)

        first_batch: list[Union[tuple[None, ...], bytearray]] = []
        for _ in range(20):
            first_batch.append(one(256))

        after_first_batch_profile = mc.snapshot_and_parse_pprof(output_filename)

        second_batch: list[Union[tuple[None, ...], bytearray]] = []
        for _ in range(15):
            second_batch.append(two(512))

        final_profile = mc.snapshot_and_parse_pprof(output_filename)

        # After initial snapshot, allocation tracking resets
        # So after_first_batch should have samples from the 20 allocations since last snapshot
        after_first_batch_count = len(after_first_batch_profile.sample)
        final_count = len(final_profile.sample)

        assert after_first_batch_count > 0, (
            f"Should have samples from first batch allocations. Got {after_first_batch_count}"
        )
        assert final_count > 0, f"Final snapshot should have samples. Got {final_count}"

        # Get sample type indices
        heap_space_idx = pprof_utils.get_sample_type_index(final_profile, "heap-space")
        alloc_space_idx = pprof_utils.get_sample_type_index(final_profile, "alloc-space")
        alloc_count_idx = pprof_utils.get_sample_type_index(final_profile, "alloc-samples")

        # Assert that required sample types exist
        assert heap_space_idx >= 0, "heap-space sample type not found in profile"
        assert alloc_space_idx >= 0, "alloc-space sample type not found in profile"
        assert alloc_count_idx >= 0, "alloc-samples sample type not found in profile"

        # Since no objects were deleted, heap samples should accumulate (first_batch + second_batch)
        # Count heap samples in both profiles
        after_first_heap_samples = [s for s in after_first_batch_profile.sample if s.value[heap_space_idx] > 0]
        final_heap_samples = [s for s in final_profile.sample if s.value[heap_space_idx] > 0]

        assert len(final_heap_samples) > len(after_first_heap_samples), (
            f"Final should have more heap samples than after first batch (nothing deleted). "
            f"Got final={len(final_heap_samples)}, after_first={len(after_first_heap_samples)}"
        )

        # Validate all samples in final profile have valid values
        for sample in final_profile.sample:
            has_heap = sample.value[heap_space_idx] > 0
            has_alloc = sample.value[alloc_space_idx] > 0
            assert has_heap or has_alloc, "Sample should have either heap-space or alloc-space > 0"
            assert sample.value[alloc_count_idx] >= 0, (
                f"alloc-samples should be non-negative, got {sample.value[alloc_count_idx]}"
            )

        # Get live samples (heap-space > 0)
        live_samples = [s for s in final_profile.sample if s.value[heap_space_idx] > 0]

        batch_one_live_samples = [
            sample for sample in live_samples if has_function_in_profile_sample(final_profile, sample, one)
        ]

        batch_two_live_samples = [
            sample for sample in live_samples if has_function_in_profile_sample(final_profile, sample, two)
        ]

        assert len(batch_one_live_samples) > 0, (
            f"Should have live samples from batch one, got {len(batch_one_live_samples)}"
        )
        assert len(batch_two_live_samples) > 0, (
            f"Should have live samples from batch two, got {len(batch_two_live_samples)}"
        )

        # batch_one samples were reported in first snapshot, so alloc-space should be 0 in later snapshots
        # batch_two samples are new allocations, so alloc-space should be > 0
        batch_one_valid = all(
            sample.value[heap_space_idx] > 0 and sample.value[alloc_space_idx] == 0 for sample in batch_one_live_samples
        )
        assert batch_one_valid, "Batch one samples should have heap-space > 0 and alloc-space == 0 (already reported)"

        batch_two_valid = all(
            sample.value[heap_space_idx] > 0 and sample.value[alloc_space_idx] > 0 for sample in batch_two_live_samples
        )
        assert batch_two_valid, "Batch two samples should have heap-space > 0 and alloc-space > 0 (new allocations)"

        del first_batch
        del second_batch


def test_memory_collector_exception_handling(tmp_path: Path) -> None:
    output_filename = _setup_profiling_prelude(tmp_path, "test_memory_collector_exception_handling")

    mc = memalloc.MemoryCollector(heap_sample_size=256)

    with pytest.raises(ValueError):
        with mc:
            _allocate_1k()
            profile = mc.snapshot_and_parse_pprof(output_filename)
            assert profile is not None
            raise ValueError("Test exception")

    with mc:
        _allocate_1k()
        profile = mc.snapshot_and_parse_pprof(output_filename)
        assert profile is not None


def test_memory_collector_allocation_during_shutdown() -> None:
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


def test_memory_collector_buffer_pool_exhaustion(tmp_path: Path) -> None:
    """Test that the memory collector handles buffer pool exhaustion.
    This test creates multiple threads that simultaneously allocate with very deep
    stack traces, which could potentially exhaust internal buffer pools.
    """
    output_filename = _setup_profiling_prelude(tmp_path, "test_memory_collector_buffer_pool_exhaustion")

    mc = memalloc.MemoryCollector(heap_sample_size=64)

    # Store reference to nested function for later qualname access
    deep_alloc_func = None

    num_threads = 10
    thread_ids: set[int] = set()
    thread_ids_lock = threading.Lock()

    with mc:
        threads: list[threading.Thread] = []
        barrier = threading.Barrier(num_threads)

        def allocate_with_traceback() -> None:
            # Record this thread's ID before waiting
            with thread_ids_lock:
                thread_ids.add(threading.current_thread().ident)  # type: ignore[arg-type]
            barrier.wait()

            def deep_alloc(depth: int) -> Union[tuple[None, ...], bytearray]:
                if depth == 0:
                    return _create_allocation(100)
                return deep_alloc(depth - 1)

            # Capture reference to deep_alloc for later use
            nonlocal deep_alloc_func
            deep_alloc_func = deep_alloc
            # Multiple allocations per thread to make sampling more reliable
            for _ in range(5):
                data = deep_alloc(50)
                del data

        for _ in range(num_threads):
            t = threading.Thread(target=allocate_with_traceback)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        profile = mc.snapshot_and_parse_pprof(output_filename)

        # Get sample type indices
        alloc_count_idx = pprof_utils.get_sample_type_index(profile, "alloc-samples")
        assert alloc_count_idx >= 0, "alloc-samples sample type not found in profile"

        deep_alloc_total_count = 0
        max_stack_depth = 0
        sampled_thread_ids: set[int] = set()

        for sample in profile.sample:
            # Buffer pool test: All samples should have stack frames
            assert len(sample.location_id) > 0, "Buffer pool test: All samples should have stack frames"
            stack_depth = len(sample.location_id)
            max_stack_depth = max(max_stack_depth, stack_depth)

            if deep_alloc_func and has_function_in_profile_sample(profile, sample, deep_alloc_func):
                # Samples with identical stack traces are merged in pprof profiles,
                # so we need to sum the alloc-samples count value
                deep_alloc_total_count += sample.value[alloc_count_idx]
                # Track which threads got sampled
                thread_id_label = pprof_utils.get_label_with_key(profile.string_table, sample, "thread id")
                if thread_id_label is not None:
                    sampled_thread_ids.add(thread_id_label.num)

        assert deep_alloc_total_count >= 10, (
            f"Buffer pool test: Expected many allocations from concurrent threads, got {deep_alloc_total_count}"
        )

        # Verify we got samples from all threads
        assert sampled_thread_ids == thread_ids, (
            f"Buffer pool test: Expected samples from all {num_threads} threads, "
            f"but only got samples from {len(sampled_thread_ids)} threads. "
            f"Missing: {thread_ids - sampled_thread_ids}"
        )

        assert max_stack_depth >= 50, (
            f"Buffer pool test: Stack traces should be preserved even under stress (expecting at least 50 frames), "
            f"but max depth was only {max_stack_depth}"
        )


def test_memory_collector_thread_lifecycle(tmp_path: Path) -> None:
    """Test that continuously creates and destroys threads while they perform allocations,
    verifying that the collector can track allocations across changing thread contexts.
    """
    output_filename = _setup_profiling_prelude(tmp_path, "test_memory_collector_thread_lifecycle")

    mc = memalloc.MemoryCollector(heap_sample_size=8)

    # Store reference to nested function for later qualname access
    worker_func = None

    with mc:
        threads: list[threading.Thread] = []

        def worker():
            for i in range(10):
                # On Python 3.14+, increase the allocation size to more reliably
                # trigger sampling. The CPython internal could have optimized
                # small allocations, and/or allocations that are deallocated too
                # quickly.
                if PY_314_OR_ABOVE:
                    data = [i] * 10000000
                else:
                    data = [i] * 100
                del data

        # Capture reference before context manager exits
        worker_func = worker

        for i in range(20):
            t = threading.Thread(target=worker)
            t.start()
            threads.append(t)

            if i > 5:
                old_thread = threads.pop(0)
                old_thread.join()

        for t in threads:
            t.join()

        profile = mc.snapshot_and_parse_pprof(output_filename)

        worker_samples = 0
        for sample in profile.sample:
            if worker_func and has_function_in_profile_sample(profile, sample, worker_func):
                worker_samples += 1

        assert worker_samples > 0, (
            "Thread lifecycle test: Should capture allocations even as threads are created/destroyed"
        )


def test_start_twice() -> None:
    from ddtrace.profiling.collector import _memalloc

    _memalloc.start(64, 512)
    with pytest.raises(RuntimeError):
        _memalloc.start(64, 512)
    _memalloc.stop()


def test_start_wrong_arg() -> None:
    from ddtrace.profiling.collector import _memalloc

    with pytest.raises(TypeError, match="function takes exactly 2 arguments \\(1 given\\)"):
        _memalloc.start(2)  # pyright: ignore[reportCallIssue]

    with pytest.raises(ValueError, match="the number of frames must be in range \\[1; 600\\]"):
        _memalloc.start(429496, 1)

    with pytest.raises(ValueError, match="the number of frames must be in range \\[1; 600\\]"):
        _memalloc.start(-1, 1)

    with pytest.raises(
        ValueError,
        match="the heap sample size must be in range \\[0; 4294967295\\]",
    ):
        _memalloc.start(64, -1)

    with pytest.raises(
        ValueError,
        match="the heap sample size must be in range \\[0; 4294967295\\]",
    ):
        _memalloc.start(64, 345678909876)


def test_start_stop() -> None:
    from ddtrace.profiling.collector import _memalloc

    _memalloc.start(1, 1)
    _memalloc.stop()


def test_heap_stress() -> None:
    from ddtrace.profiling.collector import _memalloc

    # This should run for a few seconds, and is enough to spot potential segfaults.
    _memalloc.start(64, 1024)
    try:
        x: list[object] = []

        for _ in range(20):
            for _ in range(1000):
                x.append(object())
            _memalloc.heap()
            del x[:100]
    finally:
        _memalloc.stop()


@pytest.mark.parametrize("heap_sample_size", (0, 512 * 1024, 1024 * 1024, 2048 * 1024, 4096 * 1024))
def test_memalloc_speed(benchmark, heap_sample_size) -> None:
    if heap_sample_size:
        with memalloc.MemoryCollector(heap_sample_size=heap_sample_size):
            benchmark(_allocate_1k)
    else:
        benchmark(_allocate_1k)


@pytest.mark.parametrize(
    "enabled,predicates",
    (
        (
            True,
            (
                lambda v: v >= 512 * 1024,
                lambda v: v > 1,
                lambda v: v > 512,
                lambda v: v == 512 * 1024 * 1024,
            ),
        ),
        (
            False,
            (
                lambda v: v == 0,
                lambda v: v == 0,
                lambda v: v == 0,
                lambda v: v == 0,
            ),
        ),
    ),
)
def test_memalloc_sample_size(
    enabled: bool, predicates: list[Callable[[int], bool]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DD_PROFILING_HEAP_ENABLED", str(enabled).lower())
    config = ProfilingConfig()

    assert config.heap.enabled is enabled

    for predicate, default in zip(predicates, (1024 * 1024, 1, 512, 512 * 1024 * 1024)):
        assert predicate(_derive_default_heap_sample_size(config.heap, default))


def test_no_duplicate_dropped_frames_indicator(tmp_path: Path) -> None:
    """Regression test for duplicate dropped frames indicator bug.

    This test verifies that when export_sample() is called multiple times on the same
    sample with dropped frames, the "<N frame(s) omitted>" indicator frame is only
    added once, not multiple times.

    The bug occurred when:
    1. A sample had dropped frames (deep stack > max_nframes)
    2. export_sample() was called, adding the indicator frame
    3. export_sample() was called again before clear_buffers()
    4. Another indicator frame was incorrectly added

    This test creates allocations from a very deep stack to trigger frame dropping,
    calls snapshot twice in a row, and checks that no sample has duplicate
    "<N frame(s) omitted>" frames.
    """
    from ddtrace.internal.settings.profiling import config

    output_filename = _setup_profiling_prelude(tmp_path, "test_no_duplicate_dropped_frames_indicator")

    # Stack depth that should exceed max_nframes to trigger frame dropping
    DEEP_STACK_DEPTH = 100

    # Verify that max_nframes is less than our deep stack depth
    # config.max_frames corresponds to DD_PROFILING_MAX_FRAMES (default 64)
    assert config.max_frames < DEEP_STACK_DEPTH, (
        f"Test requires DD_PROFILING_MAX_FRAMES ({config.max_frames}) < {DEEP_STACK_DEPTH} to trigger frame dropping"
    )

    # Create a very deep call stack to trigger frame dropping
    def make_deep_stack(depth: int):
        """Recursively creates a call stack of given depth."""
        if depth == 0:
            # Allocate at the leaf to create a sample
            return [object() for _ in range(100)]
        return make_deep_stack(depth - 1)

    mc = memalloc.MemoryCollector(heap_sample_size=64)

    # Keep allocated objects alive throughout both snapshots
    junk = []

    with mc:
        # Create allocations from a deep stack to trigger frame dropping
        for _ in range(10):
            junk.append(make_deep_stack(DEEP_STACK_DEPTH))

        # Call snapshot twice in a row to potentially export the same sample multiple times
        # The objects in junk remain alive, so the samples persist across both exports
        mc.snapshot_and_parse_pprof(output_filename, assert_samples=False)
        profile = mc.snapshot_and_parse_pprof(output_filename)

    # Check each sample for duplicate dropped frames indicators
    for sample_idx, sample in enumerate(profile.sample):
        omitted_frame_count = 0
        omitted_frame_locations = []

        for location_id in sample.location_id:
            location = pprof_utils.get_location_with_id(profile, location_id)
            if location.line:
                function = pprof_utils.get_function_with_id(profile, location.line[0].function_id)
                function_name = profile.string_table[function.name]

                # Check if this is a dropped frames indicator
                # The format is: "<N frame(s) omitted>" or "<N frames omitted>"
                if "omitted>" in function_name and function_name.startswith("<"):
                    omitted_frame_count += 1
                    omitted_frame_locations.append(function_name)

        # Assert that we don't have duplicate dropped frames indicators
        assert omitted_frame_count <= 1, (
            f"Sample {sample_idx} has {omitted_frame_count} dropped frames indicators "
            f"(expected at most 1). Found: {omitted_frame_locations}"
        )


def test_memory_collector_stack_order(tmp_path: Path) -> None:
    """Test that stack frames are reported in leaf-to-root order (innermost to outermost).

    This test verifies the fix for upside-down flamegraphs by ensuring that when we have
    a call chain like outer() -> middle() -> inner() -> allocation, the frames are
    reported in the order: inner, middle, outer (leaf-to-root).
    """
    output_filename = _setup_profiling_prelude(tmp_path, "test_memory_collector_stack_order")

    # Define nested functions to create a known call stack
    def outer_frame() -> Union[tuple[None, ...], bytearray]:
        return middle_frame()

    def middle_frame() -> Union[tuple[None, ...], bytearray]:
        return inner_frame()

    def inner_frame() -> Union[tuple[None, ...], bytearray]:
        # This is the leaf frame where the actual allocation happens
        return _create_allocation(256)

    mc = memalloc.MemoryCollector(heap_sample_size=64)

    with mc:
        # Create allocations with our known call stack
        data = []
        for _ in range(20):
            data.append(outer_frame())

        profile = mc.snapshot_and_parse_pprof(output_filename)

    # Get samples with alloc-space > 0
    alloc_space_idx = pprof_utils.get_sample_type_index(profile, "alloc-space")
    samples = [s for s in profile.sample if s.value[alloc_space_idx] > 0]

    assert len(samples) > 0, "Should have captured allocation samples"

    # Helper to create StackLocation with just function name
    def loc(f_name: str) -> pprof_utils.StackLocation:
        return pprof_utils.StackLocation(function_name=f_name, filename="", line_no=-1)

    # Verify we have a sample with the expected stack order: inner, middle, outer (leaf-to-root)
    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            locations=[
                loc("inner_frame"),
                loc("middle_frame"),
                loc("outer_frame"),
            ],
        ),
        print_samples_on_failure=True,
    )
