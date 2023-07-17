# -*- encoding: utf-8 -*-
import os
import time

import pytest

from ddtrace.internal import compat


TESTING_GEVENT = os.getenv("DD_PROFILE_TEST_GEVENT", False)


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
    now = compat.monotonic_ns()
    # Active wait for 2 seconds
    while compat.monotonic_ns() - now < 2e9:
        pass


def spend_cpu_3():
    # Active wait for 3 seconds
    now = compat.monotonic_ns()
    while compat.monotonic_ns() - now < 3e9:
        pass


# We allow 4% error:
# The profiler might not be precise, but time.sleep is not either.
TOLERANCE = 0.04
# Use 5% accuracy for CPU usage, it's way less precise
CPU_TOLERANCE = 0.05


def almost_equal(value, target, tolerance=TOLERANCE):
    return abs(value - target) / target <= tolerance


def total_time(time_data, funcname):
    return sum(functime[funcname] for functime in time_data.values())


@pytest.mark.subprocess(env=dict(DD_PROFILING_MAX_TIME_USAGE_PCT="100"))
def test_accuracy():
    import collections

    from ddtrace.profiling import profiler
    from ddtrace.profiling.collector import stack_event
    from tests.profiling.test_accuracy import CPU_TOLERANCE
    from tests.profiling.test_accuracy import almost_equal
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

    assert almost_equal(total_time(time_spent_ns, "spend_3"), 9e9)
    assert almost_equal(total_time(time_spent_ns, "spend_1"), 2e9)
    assert almost_equal(total_time(time_spent_ns, "spend_4"), 4e9)
    assert almost_equal(total_time(time_spent_ns, "spend_16"), 16e9)
    assert almost_equal(total_time(time_spent_ns, "spend_7"), 7e9)

    try:
        from time import monotonic_ns  # noqa
    except ImportError:
        # If we don't have access to high resolution clocks, we can't really test accurately things as it's spread in
        # various Python implementation of monotonic, etc.
        pass
    else:
        assert almost_equal(total_time(time_spent_ns, "spend_cpu_2"), 2e9)
        assert almost_equal(total_time(time_spent_ns, "spend_cpu_3"), 3e9)
        assert almost_equal(total_time(time_spent_ns, "spend_cpu_2"), 2e9, CPU_TOLERANCE)
        assert almost_equal(total_time(time_spent_ns, "spend_cpu_3"), 3e9, CPU_TOLERANCE)
