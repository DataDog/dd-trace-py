import pytest

from ddtrace.utils.counter import Counter


@pytest.fixture
def counter():
    return Counter()


def test_counter_increment(counter):
    for i in range(1, 1001):
        counter.increment()

        assert counter.value() == i

    assert counter.value() == 1000


def test_counter_decrement(counter):
    for i in range(1, 1001):
        counter.increment()
    assert counter.value() == 1000

    for i in range(999, -1001, -1):
        counter.decrement()
        assert counter.value() == i

    assert counter.value() == -1000
