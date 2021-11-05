"""
Ensure that cached functions are invalidated between test runs.
"""
import pytest

from ddtrace.internal.utils import cache


_CACHED_FUNCTIONS = []

old_cached = cache.cached


def wrapped_cached(maxsize=256):
    def wrapped_cached_f(f):
        cached_f = old_cached(maxsize)(f)
        _CACHED_FUNCTIONS.append(cached_f)
        return cached_f

    return wrapped_cached_f


cache.cached = wrapped_cached


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item, nextitem):
    for f in _CACHED_FUNCTIONS:
        f.invalidate()

    yield
