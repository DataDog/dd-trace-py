import mock
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


def test_counter_value_lock(counter):
    lock = mock.MagicMock(wraps=counter._read_lock)
    counter._read_lock = lock

    counter.increment()

    # Fetch the value using the built-in read lock
    assert counter.value() == 1

    # Assert that anything was called on "lock"
    # DEV: We could assert that `__enter__` was called, but
    #      we really just want to know "was this used at all"
    assert lock.mock_calls

    # Reset for the next validation
    lock.reset_mock()

    # Fetch the value without using the built-in read lock
    assert counter.value(no_lock=True) == 1

    # Assert nothing was called on the "lock"
    assert not lock.mock_calls
