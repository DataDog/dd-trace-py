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


def assert_almost_equal(value: float, target: float, tolerance: float = TOLERANCE) -> None:
    if abs(value - target) / target > tolerance:
        raise AssertionError(
            f"Assertion failed: {value} is not approximately equal to {target} "
            f"within tolerance={tolerance}, actual error={abs(value - target) / target}"
        )


def assert_within_tolerance(
    value: float, target: float, upper_tolerance: float = TOLERANCE, lower_tolerance: float = 0.0
) -> None:
    """Assert value >= target * (1 - lower_tolerance) and value <= target * (1 + upper_tolerance)."""
    if value < target * (1 - lower_tolerance):
        raise AssertionError(
            f"Assertion failed: {value} is less than expected minimum {target} (lower_tolerance={lower_tolerance})"
        )

    if (value - target) / target > upper_tolerance:
        raise AssertionError(
            f"Assertion failed: {value} exceeds {target} by more than {upper_tolerance * 100}%, "
            f"actual excess={((value - target) / target)}"
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
    from tests.profiling.test_accuracy import assert_within_tolerance
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

    # CPU-bound functions guarantee exact CPU time via busy-loop, but wall time
    # can exceed CPU time due to OS preemption, especially in CI environments.
    # Wall time should never be less than the target CPU time.
    assert_within_tolerance(wall_times["spend_cpu_2"], 2e9, upper_tolerance=0.3, lower_tolerance=0.01)
    assert_within_tolerance(wall_times["spend_cpu_3"], 3e9, upper_tolerance=0.3, lower_tolerance=0.01)
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
    """Return the cpu_loop iteration count that consumes ~cpu_seconds of CPU time."""
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
    median_ns = trimmed[len(trimmed) // 2]

    iterations_per_second = n / (median_ns / 1_000_000_000)
    target_iterations = cpu_seconds * iterations_per_second

    # Verify the calibration; adjust proportionally if more than ~20% off.
    start = time.thread_time_ns()
    cpu_loop(int(target_iterations))
    actual_time_ns = time.thread_time_ns() - start
    if actual_time_ns > 0:
        ratio = cpu_nano / actual_time_ns
        target_iterations = int(target_iterations * ratio)

    return int(target_iterations)


# AIDEV-NOTE: PROF-14213 — this test characterizes how badly the current
# stack profiler under-reports CPU time for short, bursty CPU work
# (10ms CPU + 100ms sleep). The assertions are deliberately permissive so
# the test does not flake CI red. The interesting number is the capture
# rate written to /tmp/test_cpu_accuracy_short_bursts.json; that is the
# baseline we want to improve with a signal-based CPU profiler.
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_cpu_accuracy_short_bursts.pprof",
        _DD_PROFILING_STACK_V2_ADAPTIVE_SAMPLING_ENABLED="0",
    ),
    # The test prints the measured capture rate; allow non-empty stdout.
    out=None,
)
def test_cpu_accuracy_short_bursts():
    import collections
    import json
    import os
    import time

    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.test_accuracy import calibrate_cpu_loop
    from tests.profiling.test_accuracy import cpu_loop

    # Calibrate cpu_loop to ~10ms CPU per call. Run calibration with the
    # profiler attached so the iteration count accounts for profiler overhead.
    p_cal = profiler.Profiler()
    p_cal.start()
    iterations = calibrate_cpu_loop(0.01)
    p_cal.stop(flush=True)

    # Discard the calibration profile so it does not pollute the measurement.
    pprof_file = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    if os.path.exists(pprof_file):
        os.remove(pprof_file)

    # Workload: 400 iterations of (10ms cpu_loop + 100ms sleep).
    # ~44s wall, ~4s ground-truth CPU on cpu_loop.
    p = profiler.Profiler()
    p.start()
    actual_cpu_time_ns = 0
    for _ in range(400):
        start_cpu = time.thread_time_ns()
        cpu_loop(iterations)
        actual_cpu_time_ns += time.thread_time_ns() - start_cpu
        time.sleep(0.1)
    p.stop()

    wall_times: "collections.defaultdict[str, int]" = collections.defaultdict(lambda: 0)
    cpu_times: "collections.defaultdict[str, int]" = collections.defaultdict(lambda: 0)
    profile = pprof_utils.parse_newest_profile(pprof_file)

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

    # The wall-time ratio cpu_loop:sleep is recorded for the artifact but not
    # asserted: cpu_loop is calibrated by CPU time, while wall time also
    # includes profiler-induced preemption of the busy loop, so the ratio
    # routinely drifts from the 0.01/0.1 duty cycle without indicating a real
    # problem. The artifact captures the number for inspection.
    sleep_function_name = None
    for func_name in wall_times.keys():
        if "sleep" in func_name.lower():
            sleep_function_name = func_name
            break

    # CPU-time measurement: this is the metric we expect to be bad.
    # Record the capture rate to a JSON artifact rather than asserting a floor;
    # under-reporting is the whole point of this test.
    profiler_cpu_ns = cpu_times["cpu_loop"]
    capture_rate = profiler_cpu_ns / actual_cpu_time_ns if actual_cpu_time_ns else 0.0
    artifact = {
        "test": "test_cpu_accuracy_short_bursts",
        "burst_ms": 10,
        "sleep_ms": 100,
        "iterations_per_burst": iterations,
        "num_bursts": 400,
        "ground_truth_cpu_ns": actual_cpu_time_ns,
        "profiler_cpu_ns": profiler_cpu_ns,
        "capture_rate": capture_rate,
        "wall_cpu_loop_ns": wall_times["cpu_loop"],
        "wall_sleep_ns": wall_times[sleep_function_name] if sleep_function_name else 0,
    }
    artifact_path = "/tmp/test_cpu_accuracy_short_bursts.json"
    with open(artifact_path, "w") as f:
        json.dump(artifact, f, indent=2)
    print(f"cpu-accuracy artifact: {artifact_path} capture_rate={capture_rate:.3f}")

    # Permissive guards: the profiler must produce some cpu-time data for
    # cpu_loop, and must not over-report by more than 10%. The actual badness
    # of the under-reporting is captured in the artifact above.
    assert profiler_cpu_ns > 0, f"profiler reported zero cpu-time for cpu_loop (ground truth {actual_cpu_time_ns} ns)"
    assert profiler_cpu_ns <= int(actual_cpu_time_ns * 1.1), (
        f"profiler cpu-time {profiler_cpu_ns} ns exceeds 110% of ground truth {actual_cpu_time_ns} ns"
    )
