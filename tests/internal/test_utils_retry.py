from itertools import count

import pytest

from ddtrace.internal.utils.retry import RetryError
from ddtrace.internal.utils.retry import fibonacci_backoff_with_jitter
from ddtrace.internal.utils.retry import retry


class NotEnough(Exception):
    pass


def test_retry_repeat():
    n = count()

    @retry(0)
    def f():
        if next(n) < 3:
            raise NotEnough()

    assert f() is None
    assert next(n) == 4


def test_retry_until():
    n = count()

    @retry(0, until=lambda r: r > 3)
    def f():
        return next(n)

    assert f() == 4
    assert next(n) == 5


def test_retry_after_iter():
    n = count()

    @retry(after=(0 for _ in range(5)))
    def f():
        return next(n)

    with pytest.raises(RetryError) as e:
        f()

    assert e.value.args[0] == 5
    assert next(n) == 6


def test_retry_after_iter_exc():
    n = count()

    class MyExc(Exception):
        pass

    @retry(after=(0 for _ in range(5)))
    def f():
        k = next(n)
        raise MyExc(k)

    with pytest.raises(MyExc) as e:
        f()

    assert e.value.args[0] == 5
    assert next(n) == 6


def test_retry_fibonacci_backoff_with_jitter():
    n = count()

    @fibonacci_backoff_with_jitter(5, initial_wait=0.0)
    def f(m):
        k = next(n)
        if k < m:
            raise NotEnough(k)

    assert f(3) is None
    assert next(n) == 4

    n = count()
    with pytest.raises(NotEnough) as e:
        f(10)
    assert e.value.args[0] == 4
