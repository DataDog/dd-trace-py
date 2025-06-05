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
    now = time.process_time_ns()
    # Active wait for 2 seconds
    while time.process_time_ns() - now < 2e9:
        pass


def spend_cpu_3():
    # Active wait for 3 seconds
    now = time.process_time_ns()
    while time.process_time_ns() - now < 3e9:
        pass


# We allow 7% error:
TOLERANCE = 0.07


def assert_almost_equal(value, target, tolerance=TOLERANCE):
    if abs(value - target) / target > tolerance:
        raise AssertionError(
            f"Assertion failed: {value} is not approximately equal to {target} "
            f"within tolerance={tolerance}, actual error={abs(value - target) / target}"
        )


def total_time(time_data, funcname):
    return sum(functime[funcname] for functime in time_data.values())


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_MAX_TIME_USAGE_PCT="100",
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_accuracy.pprof",
        DD_PROFILING_EXPORT_LIBDD_ENABLED="1",
    )
)
def test_accuracy():
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
    profile = pprof_utils.parse_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))

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

    assert_almost_equal(wall_times["spend_cpu_2"], 2e9, tolerance=0.07)
    assert_almost_equal(wall_times["spend_cpu_3"], 3e9, tolerance=0.07)
    assert_almost_equal(cpu_times["spend_cpu_2"], 2e9, tolerance=0.07)
    assert_almost_equal(cpu_times["spend_cpu_3"], 3e9, tolerance=0.07)
