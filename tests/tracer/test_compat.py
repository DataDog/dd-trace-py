# -*- coding: utf-8 -*-
# Define source file encoding to support raw unicode characters in Python 2

from hypothesis import given
from hypothesis import settings
import hypothesis.strategies as st
import pytest

from ddtrace.internal.compat import is_integer
from ddtrace.internal.compat import maybe_stringify
from ddtrace.internal.compat import to_unicode


class TestCompat(object):
    def test_to_unicode_string(self):
        # Calling `compat.to_unicode` on a non-unicode string
        res = to_unicode(b"test")
        assert type(res) == str
        assert res == "test"

    def test_to_unicode_unicode_encoded(self):
        # Calling `compat.to_unicode` on a unicode encoded string
        res = to_unicode(b"\xc3\xbf")
        assert type(res) == str
        assert res == "ÿ"

    def test_to_unicode_unicode_double_decode(self):
        # Calling `compat.to_unicode` on a unicode decoded string
        # This represents the double-decode issue, which can cause a `UnicodeEncodeError`
        #   `'\xc3\xbf'.decode('utf-8').decode('utf-8')`
        res = to_unicode(b"\xc3\xbf".decode("utf-8"))
        assert type(res) == str
        assert res == "ÿ"

    def test_to_unicode_unicode_string(self):
        # Calling `compat.to_unicode` on a unicode string
        res = to_unicode("ÿ")
        assert type(res) == str
        assert res == "ÿ"

    def test_to_unicode_bytearray(self):
        # Calling `compat.to_unicode` with a `bytearray` containing unicode
        res = to_unicode(bytearray(b"\xc3\xbf"))
        assert type(res) == str
        assert res == "ÿ"

    def test_to_unicode_bytearray_double_decode(self):
        #  Calling `compat.to_unicode` with an already decoded `bytearray`
        # This represents the double-decode issue, which can cause a `UnicodeEncodeError`
        #   `bytearray('\xc3\xbf').decode('utf-8').decode('utf-8')`
        res = to_unicode(bytearray(b"\xc3\xbf").decode("utf-8"))
        assert type(res) == str
        assert res == "ÿ"

    def test_to_unicode_non_string(self):
        #  Calling `compat.to_unicode` on non-string types
        assert to_unicode(1) == "1"
        assert to_unicode(True) == "True"
        assert to_unicode(None) == "None"
        assert to_unicode(dict(key="value")) == "{'key': 'value'}"

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
