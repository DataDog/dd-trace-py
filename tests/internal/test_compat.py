# Define source file encoding to support raw unicode characters in Python 2

import asyncio
import functools
from unittest import mock
import warnings

from hypothesis import given
from hypothesis import settings
import hypothesis.strategies as st
import pytest

from ddtrace.internal.compat import is_integer
from ddtrace.internal.compat import iscoroutinefunction
from ddtrace.internal.compat import maybe_stringify


class TestCompat:
    def test_get_connection_response(self):
        """Ensure that buffering is in kwargs."""

        class MockConn:
            def getresponse(self, *args, **kwargs):
                assert "buffering" not in kwargs

        mock = MockConn()
        mock.getresponse()


class TestPy3Compat:
    """Common tests to ensure functions are Python 3 compatible."""


@pytest.mark.parametrize(
    "obj,expected",
    [
        (1, True),
        (-1, True),
        (0, True),
        (1.0, False),
        (-1.0, False),
        (True, False),
        (False, False),
        (dict(), False),
        ([], False),
        (tuple(), False),
        (object(), False),
    ],
)
def test_is_integer(obj, expected):
    assert is_integer(obj) is expected


@given(
    obj=st.one_of(
        st.none(),
        st.booleans(),
        st.text(),
        st.complex_numbers(),
        st.dates(),
        st.integers(),
        st.decimals(),
        st.lists(st.text()),
        st.dictionaries(st.text(), st.text()),
    )
)
@settings(max_examples=100)
def test_maybe_stringify(obj):
    assert type(maybe_stringify(obj)) is (obj is not None and str or type(None))


async def _async_func():
    pass


def _sync_func():
    pass


class _AsyncCallable:
    async def __call__(self):
        pass

    async def method(self):
        pass


def _legacy_marked_func():
    pass


# asgiref (Python < 3.12) and old asyncio-style decorators tag sync callables this way.
_legacy_marked_func._is_coroutine = asyncio.coroutines._is_coroutine  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "func,expected",
    [
        (_async_func, True),
        (_sync_func, False),
        (functools.partial(_async_func), True),
        (functools.partial(functools.partial(_async_func)), True),
        (functools.partial(_sync_func), False),
        (_AsyncCallable().method, True),
        (_AsyncCallable().__call__, True),
        (_AsyncCallable(), False),
        (_AsyncCallable, False),
        (mock.AsyncMock(), True),
        (mock.MagicMock(), False),
        (mock.Mock(), False),
        (_legacy_marked_func, True),
        (None, False),
    ],
)
def test_iscoroutinefunction(func, expected):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert iscoroutinefunction(func) is expected

    with warnings.catch_warnings():
        # Deprecated in Python 3.14; compare against it while it still exists.
        warnings.simplefilter("ignore", DeprecationWarning)
        asyncio_iscoroutinefunction = getattr(asyncio, "iscoroutinefunction", None)
        if asyncio_iscoroutinefunction is not None:
            assert iscoroutinefunction(func) is asyncio_iscoroutinefunction(func)
