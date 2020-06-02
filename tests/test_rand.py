from itertools import chain
import multiprocessing as mp
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
    q = mp.Queue()
    pid = os.fork()

    # Generate random numbers in the parent and child processes after forking.
    # The child sends back their numbers to the parent where we check to see
    # if we get collisions or not.
    if pid > 0:
        # parent
        rngs = {_rand.rand64bits() for _ in range(10000)}
        child_rngs = q.get()
        q.put(None)

        if PYTHON_VERSION_INFO >= (3, 7):
            # Python 3.7+ have fork hooks which should be used
            # Hence we should not get any collisions
            assert rngs & child_rngs == set()
        else:
            # Python < 3.7 we don't have any mechanism to
            # reseed on so we expect there to be collisions.
            assert rngs == child_rngs

    else:
        # child
        try:
            rngs = {_rand.rand64bits() for _ in range(10000)}
            q.put(rngs)
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


def test_random_multiprocess():
    num_procs = 10

    q = mp.Queue()

    def target(q):
        q.put(_rand.rand64bits())

    for _ in range(num_procs):
        p = mp.Process(target=target, args=(q,))
        p.start()
        p.join()

    nums = {_rand.rand64bits()}
    while not q.empty():
        n = q.get()
        assert n not in nums
        nums.add(n)


def test_tracer_usage_fork():
    q = mp.Queue()
    pid = os.fork()

    # Similar test to test_fork() above except we use the tracer API.
    # In this case we expect to never have collisions.
    if pid > 0:
        # parent
        parent_ids_list = list(
            chain.from_iterable((s.span_id, s.trace_id) for s in [tracer.start_span("s") for _ in range(1000)])
        )
        parent_ids = set(parent_ids_list)
        assert len(parent_ids) == len(parent_ids_list), "Collisions found in parent ids"

        child_ids_list = q.get()
        q.put(None)

        child_ids = set(child_ids_list)

        assert len(child_ids) == len(child_ids_list), "Collisions found in child ids"
        assert parent_ids & child_ids == set()
    else:
        # child
        try:
            child_ids = list(
                chain.from_iterable((s.span_id, s.trace_id) for s in [tracer.start_span("s") for _ in range(1000)])
            )
            q.put(child_ids)
            q.get()
        finally:
            # Kill the process so it doesn't continue running the rest of the
            # test suite in a separate process. Note we can't use sys.exit()
            # as it raises an exception that pytest will detect as an error.
            os._exit(0)


def test_tracer_usage_multiprocess():
    num_procs = 10

    q = mp.Queue()

    def target(q):
        s = tracer.start_span("span")
        q.put(s.trace_id)
        q.put(s.span_id)

    for _ in range(num_procs):
        p = mp.Process(target=target, args=(q,))
        p.start()
        p.join()

    s = tracer.start_span("span")
    nums = {s.span_id, s.trace_id}
    while not q.empty():
        n = q.get()
        assert n not in nums
        nums.add(n)
