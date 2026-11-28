# Define source file encoding to support raw unicode characters in Python 2

from hypothesis import given
from hypothesis import settings
import hypothesis.strategies as st
import pytest

from ddtrace.internal.compat import is_integer
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


def test_iscoroutinefunction():
    import asyncio
    import functools
    import warnings
    from ddtrace.internal.compat import iscoroutinefunction

    async def async_fn():
        pass

    def sync_fn():
        pass

    class AsyncClass:
        async def method(self):
            pass

    class SyncClass:
        def method(self):
            pass

    legacy_marker = getattr(getattr(asyncio, "coroutines", None), "_is_coroutine", None)

    def legacy_fn():
        pass

    if legacy_marker is not None:
        legacy_fn._is_coroutine = legacy_marker
    else:
        legacy_fn._is_coroutine = True

    with warnings.catch_warnings(record=True) as recorded_warnings:
        warnings.simplefilter("always")

        assert iscoroutinefunction(async_fn) is True
        assert iscoroutinefunction(sync_fn) is False
        assert iscoroutinefunction(legacy_fn) is True
        assert iscoroutinefunction(AsyncClass().method) is True
        assert iscoroutinefunction(SyncClass().method) is False
        assert iscoroutinefunction(functools.partial(async_fn)) is True
        assert iscoroutinefunction(functools.partial(sync_fn)) is False

        deprecation_warnings = [
            w for w in recorded_warnings if issubclass(w.category, DeprecationWarning)
        ]
        assert not deprecation_warnings, f"Unexpected deprecation warnings: {deprecation_warnings}"

