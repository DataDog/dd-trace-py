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
