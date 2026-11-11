from collections import defaultdict
import time

import pytest


# Inclusive elapsed wall time, including preemption, is the ground truth for each
# sampled frame. Requested CPU-time budgets are not wall-time budgets.
measured_wall_ns: defaultdict[str, int] = defaultdict(int)


def spend_1() -> None:
    start_ns = time.monotonic_ns()
    time.sleep(1)
    measured_wall_ns["spend_1"] += time.monotonic_ns() - start_ns


def spend_3() -> None:
    start_ns = time.monotonic_ns()
    time.sleep(3)
    measured_wall_ns["spend_3"] += time.monotonic_ns() - start_ns


def spend_4() -> None:
    start_ns = time.monotonic_ns()
    spend_3()
    spend_1()
    measured_wall_ns["spend_4"] += time.monotonic_ns() - start_ns


def spend_7() -> None:
    start_ns = time.monotonic_ns()
    spend_3()
    spend_1()
    spend_cpu_3()
    measured_wall_ns["spend_7"] += time.monotonic_ns() - start_ns


def spend_16() -> None:
    start_ns = time.monotonic_ns()
    spend_4()
    spend_7()
    spend_cpu_2()
    spend_3()
    measured_wall_ns["spend_16"] += time.monotonic_ns() - start_ns


def spend_cpu_2() -> None:
    start_ns = time.monotonic_ns()
    # Active wait for 2 seconds
    cpu_start_ns = time.thread_time_ns()
    while time.thread_time_ns() - cpu_start_ns < 2e9:
        pass
    measured_wall_ns["spend_cpu_2"] += time.monotonic_ns() - start_ns


def spend_cpu_3() -> None:
    start_ns = time.monotonic_ns()
    # Active wait for 3 seconds
    cpu_start_ns = time.thread_time_ns()
    while time.thread_time_ns() - cpu_start_ns < 3e9:
        pass
    measured_wall_ns["spend_cpu_3"] += time.monotonic_ns() - start_ns


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
def test_accuracy_stack() -> None:
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
    wall_times: collections.defaultdict[str, int] = collections.defaultdict(lambda: 0)
    cpu_times: collections.defaultdict[str, int] = collections.defaultdict(lambda: 0)
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


def test_workloads_accumulate_inclusive_wall_time(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    ticks = iter((0, 10, 40, 50, 60, 80, 100, 140))
    monkeypatch.setattr(f"{__name__}.time", SimpleNamespace(monotonic_ns=lambda: next(ticks), sleep=lambda _: None))
    monkeypatch.setattr(f"{__name__}.measured_wall_ns", defaultdict(int))

    spend_4()
    spend_3()

    assert measured_wall_ns == {"spend_3": 70, "spend_1": 10, "spend_4": 80}


@pytest.mark.parametrize("value", (89, 111))
def test_accuracy_tolerance_rejects_outside_error_budget(value: int) -> None:
    with pytest.raises(AssertionError):
        assert_almost_equal(value, 100)
