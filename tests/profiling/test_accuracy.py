# -*- encoding: utf-8 -*-
import time

import pytest

from tests.utils import flaky


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


# We allow 5% error:
TOLERANCE = 0.07


def assert_almost_equal(value, target, tolerance=TOLERANCE):
    if abs(value - target) / target > tolerance:
        raise AssertionError(
            f"Assertion failed: {value} is not approximately equal to {target} "
            f"within tolerance={tolerance}, actual error={abs(value - target) / target}"
        )


def total_time(time_data, funcname):
    return sum(functime[funcname] for functime in time_data.values())


@pytest.mark.subprocess(env=dict(DD_PROFILING_MAX_TIME_USAGE_PCT="100"))
def test_accuracy():
    import collections

    from ddtrace.profiling import profiler
    from ddtrace.profiling.collector import stack_event
    from tests.profiling.test_accuracy import assert_almost_equal
    from tests.profiling.test_accuracy import spend_16
    from tests.profiling.test_accuracy import total_time

    # Set this to 100 so we don't sleep too often and mess with the precision.
    p = profiler.Profiler()
    # don't export data
    p._profiler._scheduler = None
    p.start()
    spend_16()
    p.stop()
    # First index is the stack position, second is the function name
    time_spent_ns = collections.defaultdict(lambda: collections.defaultdict(lambda: 0))
    cpu_spent_ns = collections.defaultdict(lambda: collections.defaultdict(lambda: 0))
    for event in p._profiler._recorder.events[stack_event.StackSampleEvent]:
        for idx, frame in enumerate(reversed(event.frames)):
            time_spent_ns[idx][frame[2]] += event.wall_time_ns
            cpu_spent_ns[idx][frame[2]] += event.cpu_time_ns

    assert_almost_equal(total_time(time_spent_ns, "spend_3"), 9e9)
    assert_almost_equal(total_time(time_spent_ns, "spend_1"), 2e9)
    assert_almost_equal(total_time(time_spent_ns, "spend_4"), 4e9)
    assert_almost_equal(total_time(time_spent_ns, "spend_16"), 16e9)
    assert_almost_equal(total_time(time_spent_ns, "spend_7"), 7e9)

    assert_almost_equal(total_time(time_spent_ns, "spend_cpu_2"), 2e9)
    assert_almost_equal(total_time(time_spent_ns, "spend_cpu_3"), 3e9)
    assert_almost_equal(total_time(cpu_spent_ns, "spend_cpu_2"), 2e9)
    assert_almost_equal(total_time(cpu_spent_ns, "spend_cpu_3"), 3e9)
