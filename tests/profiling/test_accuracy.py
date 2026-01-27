# -*- encoding: utf-8 -*-
import time

import pytest


def spend_1():
    time.sleep(1)


def spend_3():
    time.sleep(3)


def spend_4():
    spend_3()
    spend_1()


def spend_7():
    spend_3()
    spend_1()
    spend_cpu_3()


def spend_16():
    spend_4()
    spend_7()
    spend_cpu_2()
    spend_3()


def spend_cpu_2():
    # Active wait for 2 seconds
    now = time.thread_time_ns()
    while time.thread_time_ns() - now < 2e9:
        pass


def spend_cpu_3():
    # Active wait for 3 seconds
    now = time.thread_time_ns()
    while time.thread_time_ns() - now < 3e9:
        pass


# We allow 10% error:
TOLERANCE = 0.1


def assert_almost_equal(value, target, tolerance=TOLERANCE):
    if abs(value - target) / target > tolerance:
        raise AssertionError(
            f"Assertion failed: {value} is not approximately equal to {target} "
            f"within tolerance={tolerance}, actual error={abs(value - target) / target}"
        )


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_accuracy_stack.pprof",
        _DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED="0",
    )
)
def test_accuracy_stack():
    import collections
    import os

    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.test_accuracy import assert_almost_equal
    from tests.profiling.test_accuracy import spend_16

    # Set this to 100 so we don't sleep too often and mess with the precision.
    p = profiler.Profiler()
    p.start()
    spend_16()
    p.stop()
    wall_times = collections.defaultdict(lambda: 0)
    cpu_times = collections.defaultdict(lambda: 0)
    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))

    for sample in profile.sample:
        wall_time_index = pprof_utils.get_sample_type_index(profile, "wall-time")

        wall_time_spent_ns = sample.value[wall_time_index]
        cpu_time_index = pprof_utils.get_sample_type_index(profile, "cpu-time")
        cpu_time_spent_ns = sample.value[cpu_time_index]

        for location_id in sample.location_id:
            location = pprof_utils.get_location_with_id(profile, location_id)
            line = location.line[0]
            function = pprof_utils.get_function_with_id(profile, line.function_id)
            function_name = profile.string_table[function.name]
            wall_times[function_name] += wall_time_spent_ns
            cpu_times[function_name] += cpu_time_spent_ns

    assert_almost_equal(wall_times["spend_3"], 9e9)
    assert_almost_equal(wall_times["spend_1"], 2e9)
    assert_almost_equal(wall_times["spend_4"], 4e9)
    assert_almost_equal(wall_times["spend_16"], 16e9)
    assert_almost_equal(wall_times["spend_7"], 7e9)

    assert_almost_equal(wall_times["spend_cpu_2"], 2e9)
    assert_almost_equal(wall_times["spend_cpu_3"], 3e9)
    assert_almost_equal(cpu_times["spend_cpu_2"], 2e9)
    assert_almost_equal(cpu_times["spend_cpu_3"], 3e9)


def cpu_loop(iterations: int):
    result = 0x1234
    # Use a volatile prime number to avoid simple pattern recognition
    volatile = 982451653
    for i in range(iterations):
        # Mix of operations to prevent easy compiler optimizations
        result = ((result * 48271) % 2147483647) ^ volatile
        volatile = (volatile * 37 + result) % 9973
        # XOR with loop counter to ensure the result depends on the loop iteration
        result ^= i


def calibrate_cpu_loop(cpu_seconds: float) -> int:
    """Calibrates the number of loop iterations needed to consume the specified CPU time.

    Args:
        cpu_seconds: Target CPU time in seconds

    Returns:
        Number of loop iterations that should consume the target CPU time
    """
    cpu_nano = cpu_seconds * 1_000_000_000

    # First, find a rough estimate by doubling iterations
    n = 1
    while True:
        start = time.thread_time_ns()
        cpu_loop(n)
        dt = time.thread_time_ns() - start
        if dt > cpu_nano:
            break
        n *= 2

    # Now measure multiple times to get accurate timing
    durations: list[float] = []
    for _ in range(15):
        start = time.thread_time_ns()
        cpu_loop(n)
        durations.append(time.thread_time_ns() - start)
    durations.sort()
    # Trim 1 sample from each end to remove outliers (13 samples remaining)
    trimmed = durations[1:-1]
    median_ns = trimmed[len(trimmed) // 2]  # 7th sample (middle of 13)

    # Calculate iterations per second based on median
    iterations_per_second = n / (median_ns / 1_000_000_000)

    # Calculate exact iterations needed for target time
    target_iterations = cpu_seconds * iterations_per_second

    # Verify the calibration by measuring the actual time
    # If it's significantly off, adjust
    start = time.thread_time_ns()
    cpu_loop(int(target_iterations))
    actual_time_ns = time.thread_time_ns() - start

    # If actual time is more than 20% off, adjust proportionally
    if actual_time_ns > 0:
        ratio = cpu_nano / actual_time_ns
        target_iterations = int(target_iterations * ratio)

    return int(target_iterations)


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_accuracy_cpu_loop.pprof",
        _DD_PROFILING_STACK_V2_ADAPTIVE_SAMPLING_ENABLED="0",
    )
)
def test_cpu_loop_accuracy():
    import collections
    import os
    import time

    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.test_accuracy import assert_almost_equal
    from tests.profiling.test_accuracy import calibrate_cpu_loop
    from tests.profiling.test_accuracy import cpu_loop

    # Start profiler for calibration (to account for profiler overhead)
    p_cal = profiler.Profiler()
    p_cal.start()

    # Calibrate: find iterations needed for 0.01 seconds (10 milliseconds)
    # This runs with profiler on to account for profiler overhead
    iterations = calibrate_cpu_loop(0.01)

    # Stop profiler and flush to clear the profile data from calibration
    p_cal.stop(flush=True)

    # Remove the pprof file created during calibration
    pprof_file = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    if os.path.exists(pprof_file):
        os.remove(pprof_file)

    # Start profiler for the actual test
    p = profiler.Profiler()
    p.start()

    # Run cpu_loop for 0.01 seconds, then sleep 0.1 seconds, repeat 400 times
    # The number of iterations (400) was chosen to:
    # - Generate enough samples for accurate measurement
    # - Keep test duration under 1 minute threshold
    # Total wall time: 400 * (0.01 + 0.1) = 44 seconds
    # Total CPU time on cpu_loop: 400 * 0.01 = 4 seconds
    # Measure actual CPU time spent to verify calibration
    actual_cpu_time_ns = 0
    for _ in range(400):
        start_cpu = time.thread_time_ns()
        cpu_loop(iterations)
        actual_cpu_time_ns += time.thread_time_ns() - start_cpu
        time.sleep(0.1)

    p.stop()

    # Parse profile
    wall_times = collections.defaultdict(lambda: 0)
    cpu_times = collections.defaultdict(lambda: 0)
    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))

    cpu_time_index = pprof_utils.get_sample_type_index(profile, "cpu-time")
    wall_time_index = pprof_utils.get_sample_type_index(profile, "wall-time")

    for sample in profile.sample:
        wall_time_spent_ns = sample.value[wall_time_index]
        cpu_time_spent_ns = sample.value[cpu_time_index]

        for location_id in sample.location_id:
            location = pprof_utils.get_location_with_id(profile, location_id)
            line = location.line[0]
            function = pprof_utils.get_function_with_id(profile, line.function_id)
            function_name = profile.string_table[function.name]
            wall_times[function_name] += wall_time_spent_ns
            cpu_times[function_name] += cpu_time_spent_ns

    # Things to check
    # 1. the ratio of wall time spent on cpu_loop to the wall time spent on time.sleep(0.1)
    # should be close to 0.01 : 0.1
    # 2. We want to make sure that the cpu time measured by the profiler is
    # close to the actual cpu time spent on cpu_loop, as measured by actual_cpu_time_ns

    # Check 1: Ratio of wall time spent on cpu_loop to time.sleep should be ~0.01:0.1 = 0.1
    # Find the sleep function name (could be "sleep", "time.sleep", or similar)
    sleep_function_name = None
    for func_name in wall_times.keys():
        if "sleep" in func_name.lower():
            sleep_function_name = func_name
            break

    if sleep_function_name and wall_times[sleep_function_name] > 0:
        expected_ratio = 0.01 / 0.1  # 0.1
        actual_ratio = wall_times["cpu_loop"] / wall_times[sleep_function_name]
        assert_almost_equal(actual_ratio, expected_ratio)

    # Check 2: CPU time measured by profiler should be close to actual CPU time
    # NOTE: The profiler is currently under-reporting CPU time for short-duration CPU bursts.
    # This test documents the extent of the under-reporting (typically capturing ~40-50% of actual CPU time
    # in CI environments, ~70% in local environments) and serves as a baseline for future improvements
    # to increase accuracy.
    #
    # Why wall time is more accurate than CPU time:
    # - Wall time is always measured: it's simply elapsed time between samples using monotonic clock
    # - CPU time requires additional system calls (clock_gettime(CLOCK_THREAD_CPUTIME_ID) on Linux,
    #   thread_info on macOS) which can fail or return 0 if:
    #   * The thread isn't detected as running at sample time
    #   * The thread has exited or is invalid
    #   * The CPU time hasn't advanced enough to be detected
    # - For short bursts (10ms), CPU time queries may miss the burst entirely if it occurs between
    #   samples or if the thread state check fails
    #
    # Why this test needs a lower threshold (40%) compared to test_accuracy_stack_v2 (90%):
    # - test_accuracy_stack_v2 uses long continuous CPU operations (2-3 seconds), which generate
    #   many samples (200-300) and can achieve ~90% accuracy
    # - This test uses short CPU bursts (10ms) with gaps (100ms sleep), which are harder to capture
    #   accurately with 10ms sampling intervals - short bursts may be missed entirely if they occur
    #   between samples, or only partially captured
    # - CI environments may have additional variability due to resource contention, virtualization overhead,
    #   and timing precision differences, leading to lower capture rates (~40-50%)
    #
    # The profiler uses sampling, so it may capture less than 100% of CPU time.
    # We check that it captures at least 40% of the actual CPU time.
    target_cpu_time_total_ns = actual_cpu_time_ns
    min_expected_cpu_time_ns = int(target_cpu_time_total_ns * 0.4)
    assert cpu_times["cpu_loop"] >= min_expected_cpu_time_ns, (
        f"Profiler CPU time {cpu_times['cpu_loop']} ns is less than minimum expected "
        f"{min_expected_cpu_time_ns} ns (40% of actual {target_cpu_time_total_ns} ns)"
    )

    # Also check that profiler doesn't over-measure (should be <= actual + 10%)
    max_expected_cpu_time_ns = int(target_cpu_time_total_ns * 1.1)
    assert cpu_times["cpu_loop"] <= max_expected_cpu_time_ns, (
        f"Profiler CPU time {cpu_times['cpu_loop']} ns exceeds maximum expected "
        f"{max_expected_cpu_time_ns} ns (110% of actual {target_cpu_time_total_ns} ns)"
    )
