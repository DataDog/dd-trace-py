from itertools import count

import pytest

import ddtrace.internal.utils.retry as retry_module
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


def test_retry_raises_final_rejected_result_after_exception():
    outcomes = iter([NotEnough(), "final result"])

    @retry(after=[0], until=lambda result: result == "accepted")
    def f():
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    with pytest.raises(RetryError) as error:
        f()

    assert error.value.args[0] == "final result"


def test_retry_sleep_func_receives_delays():
    """A custom sleep_func is used for the initial wait and every backoff."""
    waits = []
    n = count()

    @retry(after=[1, 2], initial_wait=0.5, sleep_func=waits.append)
    def f():
        return next(n)

    with pytest.raises(RetryError):
        f()

    assert waits == [0.5, 1, 2]
    assert next(n) == 3


def test_retry_sleep_func_not_called_after_success():
    """Only the initial wait happens when the first attempt is accepted."""
    waits = []

    @retry(after=[1, 2], until=lambda r: True, sleep_func=waits.append)
    def f():
        return "ok"

    assert f() == "ok"
    assert waits == [0]


def test_retry_without_sleep_func_uses_module_sleep(monkeypatch):
    """Callers that omit sleep_func wait through the module-level sleep."""
    waits = []
    monkeypatch.setattr(retry_module, "sleep", waits.append)
    n = count()

    @retry(after=[1, 2], initial_wait=0.5)
    def f():
        return next(n)

    with pytest.raises(RetryError):
        f()

    assert waits == [0.5, 1, 2]
    assert next(n) == 3


def test_retry_sleep_func_can_stop_retrying():
    """A flag flipped during the wait lets a caller abandon the remaining attempts."""
    state = {"stop": False}
    attempts = count()

    def wait(delay):
        # Only a real backoff requests the stop; the zero initial wait must not.
        if delay:
            state["stop"] = True

    @retry(after=[1, 2], until=lambda r: state["stop"], sleep_func=wait)
    def f():
        return next(attempts)

    # Attempt 1 runs, its backoff sets the event, and `until` accepts attempt 2,
    # so the third attempt never happens.
    assert f() == 1
    assert next(attempts) == 2


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
