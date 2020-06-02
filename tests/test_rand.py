from itertools import chain
import multiprocessing as mp
import os
import threading

from ddtrace import tracer
from ddtrace.compat import PYTHON_VERSION_INFO, Queue
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


def test_multiprocess():
    q = mp.Queue()

    def target(q):
        q.put([_rand.rand64bits() for _ in range(100)])

    ps = [mp.Process(target=target, args=(q,)) for _ in range(10)]
    for p in ps:
        p.start()

    for p in ps:
        p.join()

    ids_list = [_rand.rand64bits() for _ in range(10000)]
    ids = set(ids_list)
    assert len(ids_list) == len(ids), "Collisions found in ids"

    while not q.empty():
        child_ids_list = q.get()
        child_ids = set(child_ids_list)

        assert len(child_ids_list) == len(child_ids), "Collisions found in subprocess ids"

        assert ids & child_ids == set()
        ids = ids | child_ids  # accumulate the ids


def test_threadsafe():
    # Check that the PRNG is thread-safe.
    # This obviously won't guarantee thread safety, but it's something
    # at least.
    # To provide some validation of this method I wrote a slow, unsafe RNG:
    #
    # state = 4101842887655102017
    #
    # def bad_random():
    #     global state
    #     state ^= state >> 21
    #     state ^= state << 35
    #     state ^= state >> 4
    #     return state * 2685821657736338717
    #
    # which consistently fails this test.

    q = Queue()

    def _target():
        # Generate a bunch of numbers to try to maximize the chance that
        # two threads will be calling rand64bits at the same time.
        rngs = [_rand.rand64bits() for _ in range(200000)]
        q.put(rngs)

    ts = [threading.Thread(target=_target) for _ in range(5)]

    for t in ts:
        t.start()

    for t in ts:
        t.join()

    ids = set()

    while not q.empty():
        new_ids_list = q.get()

        new_ids = set(new_ids_list)
        assert len(new_ids) == len(new_ids_list), "Collision found in ids"
        assert ids & new_ids == set()
        ids = ids | new_ids

    assert len(ids) > 0


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
    q = mp.Queue()

    # Similar to test_multiprocess(), ensures that no collisions are
    # generated between parent and child processes while using
    # multiprocessing.

    def target(q):
        ids_list = list(
            chain.from_iterable((s.span_id, s.trace_id) for s in [tracer.start_span("s") for _ in range(100)])
        )
        q.put(ids_list)

    ps = [mp.Process(target=target, args=(q,)) for _ in range(10)]
    for p in ps:
        p.start()

    for p in ps:
        p.join()

    ids_list = list(chain.from_iterable((s.span_id, s.trace_id) for s in [tracer.start_span("s") for _ in range(100)]))
    ids = set(ids_list)
    assert len(ids) == len(ids_list), "Collisions found in ids"

    while not q.empty():
        child_ids_list = q.get()
        child_ids = set(child_ids_list)

        assert len(child_ids) == len(child_ids_list), "Collisions found in subprocess ids"

        assert ids & child_ids == set()
        ids = ids | child_ids  # accumulate the ids
