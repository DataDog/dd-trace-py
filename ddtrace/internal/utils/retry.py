from __future__ import absolute_import

from functools import wraps
from itertools import repeat
import random
from time import sleep
import typing as t


class RetryError(Exception):
    pass


def retry(
    after: t.Union[int, float, t.Iterable[t.Union[int, float]]],
    until: t.Callable[[t.Any], bool] = lambda result: result is None,
    initial_wait: float = 0,
) -> t.Callable:
    def retry_decorator(f):
        @wraps(f)
        def retry_wrapped(*args, **kwargs):
            sleep(initial_wait)
            after_iter = repeat(after) if isinstance(after, (int, float)) else after
            exception = None

            for s in after_iter:
                try:
                    result = f(*args, **kwargs)
                except Exception as e:
                    exception = e
                    result = e

                if until(result):
                    return result

                sleep(s)

            # Last chance to succeed
            try:
                result = f(*args, **kwargs)
            except Exception as e:
                exception = e
                result = e

            if until(result):
                return result

            if exception is not None:
                raise exception

            raise RetryError(result)

        return retry_wrapped

    return retry_decorator


def retry_on_exceptions(
    after: t.Iterable[float],
    exceptions: t.Tuple[t.Type[BaseException], ...],
) -> t.Callable:
    def retry_decorator(f):
        @wraps(f)
        def retry_wrapped(*args, **kwargs):
            for delay in after:
                try:
                    return f(*args, **kwargs)
                except Exception as e:
                    if not isinstance(e, exceptions):
                        raise  # Not a retriable exception, don't keep retrying.
                    sleep(delay)

            # Last chance to succeed. If it fails, we don't catch the exception.
            return f(*args, **kwargs)

        return retry_wrapped

    return retry_decorator


def fibonacci_backoff_with_jitter(attempts, initial_wait=1.0, until=lambda result: result is None):
    # type: (int, float, t.Callable[[t.Any], bool]) -> t.Callable
    return retry(
        after=[random.uniform(0, initial_wait * (1.618**i)) for i in range(attempts - 1)],  # nosec
        until=until,
    )


def fibonacci_backoff_with_jitter_on_exceptions(
    attempts: int, exceptions: t.Tuple[t.Type[BaseException], ...]
) -> t.Callable:
    return retry_on_exceptions(
        after=[random.uniform(0, 1.618**i) for i in range(attempts - 1)],  # nosec
        exceptions=exceptions,
    )
