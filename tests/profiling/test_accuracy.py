# -*- encoding: utf-8 -*-
import functools
import time

import pytest


# Inclusive elapsed wall time, including preemption, is the ground truth for each
# sampled frame. Requested CPU-time budgets are not wall-time budgets.
measured_wall_ns: dict[str, int] = {}


def _measure_wall(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.monotonic_ns()
        try:
            return func(*args, **kwargs)
        finally:
            measured_wall_ns[func.__name__] = measured_wall_ns.get(func.__name__, 0) + (time.monotonic_ns() - start)

    return wrapper


@_measure_wall
def spend_1():
    time.sleep(1)


@_measure_wall
def spend_3():
    time.sleep(3)


@_measure_wall
def spend_4():
    spend_3()
    spend_1()


@_measure_wall
def spend_7():
    spend_3()
    spend_1()
    spend_cpu_3()


@_measure_wall
def spend_16():
    spend_4()
    spend_7()
    spend_cpu_2()
    spend_3()


@_measure_wall
def spend_cpu_2():
    # Active wait for 2 seconds
    now = time.thread_time_ns()
    while time.thread_time_ns() - now < 2e9:
        pass


@_measure_wall
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
    from tests.profiling.test_accuracy import measured_wall_ns
    from tests.profiling.test_accuracy import spend_16

    measured_wall_ns.clear()
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

    # Include preemption in the wall-time target without changing the sampling error budget.
    for name in ("spend_1", "spend_3", "spend_4", "spend_7", "spend_16", "spend_cpu_2", "spend_cpu_3"):
        assert_almost_equal(wall_times[name], measured_wall_ns[name])

    # CPU-bound functions guarantee exact CPU time via busy-loop measured against
    # thread_time_ns, independent of preemption, so the cpu-time targets stay fixed.
    assert_almost_equal(cpu_times["spend_cpu_2"], 2e9)
    assert_almost_equal(cpu_times["spend_cpu_3"], 3e9)


def test_measure_wall_accumulates_inclusive_intervals(monkeypatch):
    from types import SimpleNamespace

    ticks = iter((0, 10, 40, 60, 100, 140))
    monkeypatch.setattr(f"{__name__}.time", SimpleNamespace(monotonic_ns=lambda: next(ticks)))
    monkeypatch.setattr(f"{__name__}.measured_wall_ns", {})

    @_measure_wall
    def inner(value):
        return value

    @_measure_wall
    def outer():
        return inner(7)

    assert outer() == 7
    assert inner(9) == 9
    assert measured_wall_ns == {"inner": 70, "outer": 60}


@pytest.mark.parametrize("value", (89, 111))
def test_accuracy_tolerance_rejects_outside_error_budget(value):
    with pytest.raises(AssertionError):
        assert_almost_equal(value, 100)
