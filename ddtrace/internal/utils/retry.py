from __future__ import absolute_import

from functools import wraps
from itertools import repeat
import random
from time import sleep
import typing as t


_NO_RESULT = object()


class RetryError(Exception):
    pass


def retry(after, until=lambda result: result is None):
    # type: (t.Union[int, float, t.Iterable[t.Union[int, float]]], t.Callable[[t.Any], bool]) -> t.Callable
    def retry_decorator(f):
        @wraps(f)
        def retry_wrapped(*args, **kwargs):
            after_iter = repeat(after) if isinstance(after, (int, float)) else after
            result = _NO_RESULT

            for s in after_iter:
                try:
                    result = f(*args, **kwargs)
                except Exception as e:
                    result = e

                if until(result):
                    return result

                sleep(s)

            # Last chance to succeed
            try:
                result = f(*args, **kwargs)
            except Exception as e:
                result = e

            if until(result):
                return result

            raise result if isinstance(result, Exception) else RetryError(result)

        return retry_wrapped

    return retry_decorator


def fibonacci_backoff_with_jitter(attempts, initial_wait=1.0, until=lambda result: result is None):
    # type: (int, float, t.Callable[[t.Any], bool]) -> t.Callable
    return retry(
        after=[random.uniform(0, initial_wait * (1.618 ** i)) for i in range(attempts - 1)],  # nosec
        until=until,
    )
