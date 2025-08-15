# -*- coding: utf-8 -*-
# Define source file encoding to support raw unicode characters in Python 2

from hypothesis import given
from hypothesis import settings
import hypothesis.strategies as st
import pytest

from ddtrace.internal.compat import is_integer
from ddtrace.internal.compat import maybe_stringify


class TestCompat(object):
    def test_get_connection_response(self):
        """Ensure that buffering is in kwargs."""

        class MockConn(object):
            def getresponse(self, *args, **kwargs):
                assert "buffering" not in kwargs

        mock = MockConn()
        mock.getresponse()


class TestPy3Compat(object):
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
