from multiprocessing import Queue
import os

from ddtrace import tracer
from ddtrace.compat import PYTHON_VERSION_INFO
from ddtrace.internal import _rand


def test_random():
    m = set()
    for i in range(0, 2 ** 16):
        n = _rand.rand64bits()
        assert 0 <= n <= 2 ** 64 - 1
        assert n not in m
        m.add(n)


def test_fork():
    q = Queue()
    pid = os.fork()
    if pid > 0:
        # parent
        rngs = {_rand.rand64bits() for _ in range(100)}
        child_rngs = q.get()
        q.put(None)

        if PYTHON_VERSION_INFO >= (3, 7):
            # Python 3.7+ have fork hooks which should be used
            # Hence we should not get any collisions
            assert not rngs & child_rngs
        else:
            # Python < 3.7 we don't have any mechanism to
            # reseed on so we expect there to be collisions.
            assert rngs == child_rngs

    else:
        # child
        try:
            rngs = {_rand.rand64bits() for _ in range(100)}
            q.put(rngs)
            q.get()
        finally:
            # Kill the process so it doesn't continue running the rest of the
            # test suite in a separate process. Note we can't use sys.exit()
            # as it raises an exception that pytest will detect as an error.
            os._exit(0)


def test_tracer_usage_fork():
    q = Queue()
    pid = os.fork()
    if pid > 0:
        # parent
        span = tracer.start_span("span")
        child_ids = q.get()
        q.put(None)
        assert not {span.span_id, span.trace_id} & child_ids
    else:
        # child
        try:
            s = tracer.start_span("span")
            q.put({s.span_id, s.trace_id})
            q.get()
        finally:
            # Kill the process so it doesn't continue running the rest of the
            # test suite in a separate process. Note we can't use sys.exit()
            # as it raises an exception that pytest will detect as an error.
            os._exit(0)


def test_patching_random_seed():
    import random

    seed = _rand._getstate()
    random.seed()
    assert _rand._getstate() != seed
