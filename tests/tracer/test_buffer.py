import pytest

from ddtrace.internal.buffer import BufferFull
from ddtrace.internal.buffer import BufferItemTooLarge
from ddtrace.internal.buffer import TraceBuffer


def test_buffer_put_get():
    buf = TraceBuffer(max_size=10, max_item_size=3)
    buf.put([1, 2, 3])
    assert len(buf) == 1
    assert buf.size == 3

    buf.put([3, 2])
    assert len(buf) == 2
    assert buf.size == 5

    items = buf.get()
    assert len(buf) == 0
    assert buf.size == 0
    assert items == [[1, 2, 3], [3, 2]]

    buf.put("123")
    buf.put("45")
    buf.put("67")
    buf.put("89")
    buf.put("0")
    assert buf.size == 10
    assert len(buf) == 5
    assert buf.get() == ["123", "45", "67", "89", "0"]
    assert buf.size == 0


def test_buffer_size_limit():
    buf = TraceBuffer(max_size=10, max_item_size=3)
    buf.put([3])
    for i in range(9):
        buf.put([0])

    with pytest.raises(BufferFull):
        buf.put([1])

    with pytest.raises(BufferFull):
        buf.put([1])


def test_buffer_item_size_limit():
    buf = TraceBuffer(max_size=10, max_item_size=3)

    with pytest.raises(BufferItemTooLarge):
        buf.put([i for i in range(10000)])

    with pytest.raises(BufferItemTooLarge):
        buf.put([1, 2, 3, 4])

    buf.put([1, 2, 3])
    assert buf.size == 3


def test_buffer_many_items():
    buf = TraceBuffer(max_size=10000, max_item_size=50)
    for _ in range(10000):
        buf.put("1")
    assert buf.size == 10000
    assert len(buf) == 10000

    items = buf.get()
    assert len(items) == 10000
    for i in items:
        assert i == "1"

    for _ in range(200):
        buf.put("1" * 50)
    assert buf.size == 10000
    assert len(buf) == 200

    with pytest.raises(BufferFull):
        buf.put("1")
    items = buf.get()
    assert len(items) == 200
    for i in items:
        assert i == "1" * 50
