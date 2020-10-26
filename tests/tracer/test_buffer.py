import pytest

from ddtrace.internal.buffer import TraceBuffer


def test_buffer_put_get():
    buf = TraceBuffer(max_size=10, max_item_size=3)
    buf.put([1, 2, 3])
    assert len(buf) == 1
    assert buf.size == 3

    buf.put([3, 2])
    assert len(buf) == 2
    assert buf.size == 5

    items = buf.get(5)
    assert len(buf) == 0
    assert buf.size == 0
    assert items == [[1, 2, 3], [3, 2]]


def test_buffer_size_limit():
    buf = TraceBuffer(max_size=10, max_item_size=3)
    buf.put([3])
    for i in range(9):
        buf.put([0])

    with pytest.raises(TraceBuffer.BufferFull):
        buf.put([1])


def test_buffer_item_size_limit():
    buf = TraceBuffer(max_size=10, max_item_size=3)

    with pytest.raises(TraceBuffer.BufferItemTooLarge):
        buf.put([i for i in range(10000)])

    with pytest.raises(TraceBuffer.BufferItemTooLarge):
        buf.put([1, 2, 3, 4])

    buf.put([1, 2, 3])


def test_buffer_partial_get():
    buf = TraceBuffer(max_size=10, max_item_size=3)
    buf.put([1, 2, 3])
    buf.put([4, 5, 6])
    items = buf.get(4)
    assert items == [[1, 2, 3]]
    items = buf.get(4)
    assert items == [[4, 5, 6]]

    buf = TraceBuffer(max_size=10, max_item_size=3)
    buf.put([1, 2, 3])
    buf.put([4, 5, 6])
    items = buf.get(10)
    assert items == [[1, 2, 3], [4, 5, 6]]

    buf = TraceBuffer(max_size=10, max_item_size=3)
    buf.put([1, 2, 3])
    buf.put([4, 5, 6])
    items = buf.get(6)
    assert items == [[1, 2, 3], [4, 5, 6]]

    buf = TraceBuffer(max_size=10, max_item_size=3)
    buf.put([1, 2, 3])
    buf.put([4, 5, 6])
    items = buf.get(3)
    assert items == [[1, 2, 3]]
