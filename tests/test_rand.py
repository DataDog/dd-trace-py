from multiprocessing import Queue
import os
import time

from ddtrace import tracer
from ddtrace.internal import _rand


def test_random():
    m = set()
    for i in range(0, 2 ** 16):
        n = _rand.rand64bits()
        assert 0 <= n <= 2 ** 64 - 1
        assert n not in m
        m.add(n)


def test_not_fork_safe():
    q = Queue()
    pid = os.fork()
    if pid > 0:
        # parent
        rngs = {_rand.rand64bits() for i in range(100)}
        child_rngs = q.get()
        assert rngs == child_rngs
    else:
        # child
        rngs = {_rand.rand64bits() for i in range(100)}
        q.put(rngs)

        # Kill the process so it doesn't continue running the rest of the
        # test suite in a separate process. Note we can't use sys.exit()
        # https://stackoverflow.com/a/21705694
        time.sleep(1)
        os._exit(0)


def test_tracer_usage_fork():
    q = Queue()
    pid = os.fork()
    if pid > 0:
        # parent
        span = tracer.start_span("span")
        child_ids = q.get()
        assert not {span.span_id, span.trace_id} & child_ids
    else:
        # child
        s = tracer.start_span("span")
        q.put({s.span_id, s.trace_id})

        # Kill the process so it doesn't continue running the rest of the
        # test suite in a separate process. Note we can't use sys.exit()
        # https://stackoverflow.com/a/21705694
        time.sleep(1)
        os._exit(0)


def test_patching_random_seed():
    import random

    seed = _rand._getstate()
    random.seed()
    assert _rand._getstate() != seed
