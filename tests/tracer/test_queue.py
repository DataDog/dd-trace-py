from ddtrace.internal._queue import TraceQueue


def test_queue_no_limit():
    q = TraceQueue()
    for i in range(0, 10000):
        q.put([i])

    items = q.get()
    assert len(items) == 10000
    for i in range(0, 10000):
        assert items[i] == [i]

    dropped, accepted, lengths = q.pop_stats()
    assert dropped == 0
    assert accepted == 10000
    assert lengths == 10000


def test_queue_full():
    q = TraceQueue(maxsize=3)
    q.put([1])
    q.put(2)
    q.put([3])
    q.put([4, 4])
    assert len(q) == 3

    state = list(q._queue)
    assert state == [[1], 2, [4, 4]] or state == [[1], [4, 4], [3]] or state == [[4, 4], 2, [3]]
    assert q._dropped == 1
    assert q._accepted == 4
    assert q._accepted_lengths == 5

    dropped, accepted, accepted_lengths = q.pop_stats()
    assert dropped == 1
    assert accepted == 4
    assert accepted_lengths == 5


def test_queue_get():
    q = TraceQueue(maxsize=3)
    q.put(1)
    q.put(2)
    assert q.get() == [1, 2]
    assert q.get() == []


def test_multiple_queues():
    q1 = TraceQueue()
    q2 = TraceQueue()

    q1.put(1)
    q2.put(1)

    assert len(q1) == 1
    assert len(q2) == 1

    q1.get()
    assert len(q1) == 0
    assert len(q2) == 1

    q2.get()
    assert len(q1) == 0
    assert len(q2) == 0


def test_queue_overflow():
    q = TraceQueue(maxsize=1000)

    for i in range(10000):
        q.put([])
        assert len(q) <= 1000

    dropped, accepted, accepted_lengths = q.pop_stats()
    assert dropped == 9000
    assert accepted == 10000
    assert accepted_lengths == 0
