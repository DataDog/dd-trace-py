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
    sleep_func: t.Optional[t.Callable[[float], t.Any]] = None,
) -> t.Callable:
    """Retry ``f`` until ``until`` accepts its result, waiting ``after`` between attempts.

    ``sleep_func`` overrides how the waits are performed, and is resolved on every
    call so that the module-level ``sleep`` remains patchable. It defaults to
    ``time.sleep``; a caller running on a background thread can pass an
    interruptible wait instead, so that a pending shutdown does not have to sit
    through the remaining backoff.
    """

    def retry_decorator(f):
        @wraps(f)
        def retry_wrapped(*args, **kwargs):
            _sleep = sleep if sleep_func is None else sleep_func
            _sleep(initial_wait)
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

                _sleep(s)

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


def fibonacci_backoff_with_jitter(attempts, initial_wait=1.0, until=lambda result: result is None):
    # type: (int, float, t.Callable[[t.Any], bool]) -> t.Callable
    return retry(
        after=[random.uniform(0, initial_wait * (1.618**i)) for i in range(attempts - 1)],  # nosec
        until=until,
    )
