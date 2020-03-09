import os
import threading
import time
import timeit

import pytest

from ddtrace.vendor import six
from ddtrace.vendor.six.moves import _thread

from ddtrace.profile import recorder
from ddtrace.profile.collector import stack

from . import test_collector

TESTING_GEVENT = os.getenv("DD_PROFILE_TEST_GEVENT", False)

try:
    from gevent import monkey
except ImportError:
    sleep = time.sleep
else:
    sleep = monkey.get_original("time", "sleep")


def func1():
    return func2()


def func2():
    return func3()


def func3():
    return func4()


def func4():
    return func5()


def func5():
    return sleep(1)


def test_collect_truncate():
    r = recorder.Recorder()
    c = stack.StackCollector(r, nframes=5)
    c.start()
    func1()
    while not r.events[stack.StackSampleEvent]:
        pass
    c.stop()
    e = r.events[stack.StackSampleEvent][0]
    assert e.nframes > c.nframes
    assert len(e.frames) == c.nframes


def test_collect_once():
    r = recorder.Recorder()
    s = stack.StackCollector(r)
    s._init()
    all_events = s._collect()
    assert len(all_events) == 2
    e = all_events[0][0]
    assert e.thread_id > 0
    # Thread name is None with gevent
    assert isinstance(e.thread_name, (str, type(None)))
    assert len(e.frames) > 1
    assert e.frames[0][0].endswith(".py")
    assert e.frames[0][1] > 0
    assert isinstance(e.frames[0][2], str)


def test_max_time_usage():
    r = recorder.Recorder()
    with pytest.raises(ValueError):
        stack.StackCollector(r, max_time_usage_pct=0)


def test_max_time_usage_over():
    r = recorder.Recorder()
    with pytest.raises(ValueError):
        stack.StackCollector(r, max_time_usage_pct=200)


def test_ignore_profiler():
    r, c, thread_id = test_collector._test_collector_collect(stack.StackCollector, stack.StackSampleEvent)
    events = r.events[stack.StackSampleEvent]
    assert thread_id not in {e.thread_id for e in events}


def test_no_ignore_profiler():
    r, c, thread_id = test_collector._test_collector_collect(
        stack.StackCollector, stack.StackSampleEvent, ignore_profiler=False
    )
    events = r.events[stack.StackSampleEvent]
    assert thread_id in {e.thread_id for e in events}


def test_collect():
    test_collector._test_collector_collect(stack.StackCollector, stack.StackSampleEvent)


def test_status():
    test_collector._test_collector_status(stack.StackCollector)


def test_repr():
    test_collector._test_repr(
        stack.StackCollector,
        "StackCollector(recorder=Recorder(max_size=49152), max_time_usage_pct=2.0, "
        "nframes=64, ignore_profiler=True)",
    )


def test_new_interval():
    r = recorder.Recorder()
    c = stack.StackCollector(r)
    new_interval = c._compute_new_interval(1000000)
    assert new_interval == 0.049
    new_interval = c._compute_new_interval(2000000)
    assert new_interval == 0.098
    c = stack.StackCollector(r, max_time_usage_pct=10)
    new_interval = c._compute_new_interval(200000)
    assert new_interval == 0.01
    new_interval = c._compute_new_interval(1)
    assert new_interval == c.MIN_INTERVAL_TIME


# Function to use for stress-test of polling
MAX_FN_NUM = 30
FN_TEMPLATE = """def _f{num}():
  return _f{nump1}()"""

for num in range(MAX_FN_NUM):
    if six.PY3:
        exec(FN_TEMPLATE.format(num=num, nump1=num + 1))
    else:
        exec(FN_TEMPLATE.format(num=num, nump1=num + 1))

exec(
    """def _f{MAX_FN_NUM}():
    try:
      raise ValueError('test')
    except Exception:
      sleep(2)""".format(
        MAX_FN_NUM=MAX_FN_NUM
    )
)


@pytest.mark.skipif(TESTING_GEVENT, reason="Test not compatible with gevent")
def test_stress_threads():
    NB_THREADS = 20

    threads = []
    for i in range(NB_THREADS):
        t = threading.Thread(target=_f0)  # noqa: E149,F821
        t.start()
        threads.append(t)

    s = stack.StackCollector(recorder=recorder.Recorder())
    s._init()
    number = 10000
    exectime = timeit.timeit(s.collect, number=number)
    print("%.3f ms per call" % (1000.0 * exectime / number))
    for t in threads:
        t.join()


@pytest.mark.skipif(not stack.FEATURES["stack-exceptions"], reason="Stack exceptions not supported")
@pytest.mark.skipif(TESTING_GEVENT, reason="Test not compatible with gevent")
def test_exception_collection_threads():
    NB_THREADS = 5

    threads = []
    for i in range(NB_THREADS):
        t = threading.Thread(target=_f0)  # noqa: E149,F821
        t.start()
        threads.append(t)

    r, c, thread_id = test_collector._test_collector_collect(stack.StackCollector, stack.StackExceptionSampleEvent)
    exception_events = r.events[stack.StackExceptionSampleEvent]
    e = exception_events[0]
    assert e.timestamp > 0
    assert e.sampling_period > 0
    assert e.thread_id in {t.ident for t in threads}
    assert isinstance(e.thread_name, str)
    assert e.frames == [("<string>", 5, "_f30")]
    assert e.nframes == 1
    assert e.exc_type == ValueError
    for t in threads:
        t.join()


@pytest.mark.skipif(not stack.FEATURES["stack-exceptions"], reason="Stack exceptions not supported")
def test_exception_collection():
    r = recorder.Recorder()
    c = stack.StackCollector(r)
    c.start()
    try:
        raise ValueError("hello")
    except Exception:
        sleep(1)
    c.stop()

    exception_events = r.events[stack.StackExceptionSampleEvent]
    assert len(exception_events) >= 1
    e = exception_events[0]
    assert e.timestamp > 0
    assert e.sampling_period > 0
    if not TESTING_GEVENT:
        assert e.thread_id == _thread.get_ident()
        assert e.thread_name == "MainThread"
    assert e.frames == [(__file__, 205, "test_exception_collection")]
    assert e.nframes == 1
    assert e.exc_type == ValueError
