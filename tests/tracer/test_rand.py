from itertools import chain
import multiprocessing as mp

import pytest


try:
    from multiprocessing import SimpleQueue as MPQueue
except ImportError:
    from multiprocessing.queues import SimpleQueue as MPQueue

import threading
import time

from ddtrace import tracer
from ddtrace.internal import _rand
from ddtrace.internal import forksafe
from ddtrace.internal.compat import Queue


def test_random():
    m = set()
    for _ in range(0, 2**16):
        n = _rand.rand64bits()
        assert 0 <= n <= 2**64 - 1
        assert n not in m
        m.add(n)


def test_rand128bit():
    """This test ensures 128bit integers are generated with the following format:
    <32-bit unix seconds><32 bits of zero><64 random bits>
    """
    # This test validates the timestamp set in the trace id.
    # To avoid random test failures t1 and t2 are set to an interval of 2 at least seconds.
    t1 = int(time.time()) - 1
    val1 = _rand.rand128bits()
    val2 = _rand.rand128bits()
    t2 = int(time.time()) + 1

    val1_as_binary = format(val1, "b")
    rand_64bit1 = int(val1_as_binary[-64:], 2)
    zeros1 = int(val1_as_binary[-96:-64], 2)
    unix_time1 = int(val1_as_binary[:-96], 2)

    val2_as_binary = format(val2, "b")
    rand_64bit2 = int(val2_as_binary[-64:], 2)
    zeros2 = int(val2_as_binary[-96:-64], 2)
    unix_time2 = int(val2_as_binary[:-96], 2)

    # Assert that 64 lowest order bits of the 128 bit integers are random
    assert 0 <= rand_64bit1 <= 2**64 - 1
    assert 0 <= rand_64bit2 <= 2**64 - 1
    assert rand_64bit1 != rand_64bit2

    # Assert that bits 64 to 96 are zeros (from least significant to most)
    assert zeros1 == zeros2 == 0

    # Assert that the 32 most significant bits is unix time in seconds
    assert t1 <= unix_time1 <= t2
    assert t1 <= unix_time2 <= t2


@pytest.mark.subprocess()
def test_fork_no_pid_check():
    import os

    from ddtrace.internal import _rand
    from tests.tracer.test_rand import MPQueue

    q = MPQueue()
    pid = os.fork()

    # Generate random numbers in the parent and child processes after forking.
    # The child sends back their numbers to the parent where we check to see
    # if we get collisions or not.
    if pid > 0:
        # parent
        rns = {_rand.rand64bits() for _ in range(100)}
        child_rns = q.get()

        assert rns & child_rns == set()

    else:
        # child
        try:
            rngs = {_rand.rand64bits() for _ in range(100)}
            q.put(rngs)
        finally:
            # Kill the process so it doesn't continue running the rest of the
            # test suite in a separate process. Note we can't use sys.exit()
            # as it raises an exception that pytest will detect as an error.
            os._exit(0)


@pytest.mark.subprocess()
def test_fork_pid_check():
    import os

    from ddtrace.internal import _rand
    from tests.tracer.test_rand import MPQueue

    q = MPQueue()
    pid = os.fork()

    # Generate random numbers in the parent and child processes after forking.
    # The child sends back their numbers to the parent where we check to see
    # if we get collisions or not.
    if pid > 0:
        # parent
        rns = {_rand.rand64bits() for _ in range(100)}
        child_rns = q.get()

        assert rns & child_rns == set()

    else:
        # child
        try:
            rngs = {_rand.rand64bits() for _ in range(100)}
            q.put(rngs)
        finally:
            # Kill the process so it doesn't continue running the rest of the
            # test suite in a separate process. Note we can't use sys.exit()
            # as it raises an exception that pytest will detect as an error.
            os._exit(0)


def _test_multiprocess_target(q):
    assert sum((_ is _rand.seed for _ in forksafe._registry)) == 1
    q.put([_rand.rand64bits() for _ in range(100)])


def test_multiprocess():
    q = MPQueue()

    ps = [mp.Process(target=_test_multiprocess_target, args=(q,)) for _ in range(30)]
    for p in ps:
        p.start()

    for p in ps:
        p.join(60)
        if p.exitcode != 0:
            return  # this can happen occasionally. ideally this test would `assert p.exitcode == 0`.

    ids_list = [_rand.rand64bits() for _ in range(1000)]
    ids = set(ids_list)
    assert len(ids_list) == len(ids), "Collisions found in ids"

    while not q.empty():
        child_ids_list = q.get()
        child_ids = set(child_ids_list)

        assert len(child_ids_list) == len(child_ids), "Collisions found in subprocess ids"

        assert ids & child_ids == set()
        ids = ids | child_ids  # accumulate the ids


def _test_threadsafe_target(q):
    # Generate a bunch of numbers to try to maximize the chance that
    # two threads will be calling rand64bits at the same time.
    rngs = [_rand.rand64bits() for _ in range(200000)]
    q.put(rngs)


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

    ts = [threading.Thread(target=_test_threadsafe_target, args=(q,)) for _ in range(5)]

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


@pytest.mark.subprocess
def test_tracer_usage_fork():
    from itertools import chain
    import os

    from tests.tracer.test_rand import MPQueue
    from tests.tracer.test_rand import _get_ids

    q = MPQueue()
    pid = os.fork()

    # Similar test to test_fork() above except we use the tracer API.
    # In this case we expect to never have collisions.
    if pid > 0:
        # parent
        parent_ids_list = list(chain.from_iterable(ids for ids in [_get_ids() for _ in range(100)]))
        parent_ids = set(parent_ids_list)
        assert len(parent_ids) == len(parent_ids_list), "Collisions found in parent process ids"

        child_ids_list = q.get()

        child_ids = set(child_ids_list)

        assert len(child_ids) == len(child_ids_list), "Collisions found in child process ids"
        assert parent_ids & child_ids == set()
    else:
        # child
        try:
            child_ids = list(chain.from_iterable(ids for ids in [_get_ids() for _ in range(100)]))
            q.put(child_ids)
        finally:
            # Kill the process so it doesn't continue running the rest of the
            # test suite in a separate process. Note we can't use sys.exit()
            # as it raises an exception that pytest will detect as an error.
            os._exit(0)


def _get_ids():
    with tracer.start_span("s") as s:
        return s.span_id, s.trace_id


def _test_tracer_usage_multiprocess_target(q):
    ids_list = list(chain.from_iterable(ids for ids in [_get_ids() for _ in range(100)]))
    q.put(ids_list)


def test_tracer_usage_multiprocess():
    q = MPQueue()

    # Similar to test_multiprocess(), ensures that no collisions are
    # generated between parent and child processes while using
    # multiprocessing.

    # Note that we have to be wary of the size of the underlying
    # pipe in the queue: https://bugs.python.org/msg143081
    ps = [mp.Process(target=_test_tracer_usage_multiprocess_target, args=(q,)) for _ in range(3)]
    for p in ps:
        p.start()

    for p in ps:
        p.join(60)

    ids_list = list(chain.from_iterable(ids for ids in [_get_ids() for _ in range(100)]))
    ids = set(ids_list)
    assert len(ids) == len(ids_list), "Collisions found in ids"

    while not q.empty():
        child_ids_list = q.get()
        child_ids = set(child_ids_list)

        assert len(child_ids) == len(child_ids_list), "Collisions found in subprocess ids"

        assert ids & child_ids == set()
        ids = ids | child_ids  # accumulate the ids


@pytest.mark.subprocess
def test_span_api_fork():
    from itertools import chain
    import os

    from ddtrace._trace.span import Span
    from tests.tracer.test_rand import MPQueue

    q = MPQueue()
    pid = os.fork()

    if pid > 0:
        # parent
        parent_ids_list = list(chain.from_iterable((s.span_id, s.trace_id) for s in [Span(None) for _ in range(100)]))
        parent_ids = set(parent_ids_list)
        assert len(parent_ids) == len(parent_ids_list), "Collisions found in parent process ids"

        child_ids_list = q.get()

        child_ids = set(child_ids_list)

        assert len(child_ids) == len(child_ids_list), "Collisions found in child process ids"
        assert parent_ids & child_ids == set()
    else:
        # child
        try:
            child_ids = list(chain.from_iterable((s.span_id, s.trace_id) for s in [Span(None) for _ in range(100)]))
            q.put(child_ids)
        finally:
            os._exit(0)
