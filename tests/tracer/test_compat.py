# -*- coding: utf-8 -*-
# Define source file encoding to support raw unicode characters in Python 2
import sys

from hypothesis import given
from hypothesis import settings
import hypothesis.strategies as st
import pytest
import six

from ddtrace.internal.compat import PY2
from ddtrace.internal.compat import PY3
from ddtrace.internal.compat import get_connection_response
from ddtrace.internal.compat import is_integer
from ddtrace.internal.compat import maybe_stringify
from ddtrace.internal.compat import reraise
from ddtrace.internal.compat import to_unicode


if PY3:
    unicode = str


class TestCompat(object):
    def test_to_unicode_string(self):
        # Calling `compat.to_unicode` on a non-unicode string
        res = to_unicode(b"test")
        assert type(res) == unicode
        assert res == "test"

    def test_to_unicode_unicode_encoded(self):
        # Calling `compat.to_unicode` on a unicode encoded string
        res = to_unicode(b"\xc3\xbf")
        assert type(res) == unicode
        assert res == u"ÿ"

    def test_to_unicode_unicode_double_decode(self):
        # Calling `compat.to_unicode` on a unicode decoded string
        # This represents the double-decode issue, which can cause a `UnicodeEncodeError`
        #   `'\xc3\xbf'.decode('utf-8').decode('utf-8')`
        res = to_unicode(b"\xc3\xbf".decode("utf-8"))
        assert type(res) == unicode
        assert res == u"ÿ"

    def test_to_unicode_unicode_string(self):
        # Calling `compat.to_unicode` on a unicode string
        res = to_unicode(u"ÿ")
        assert type(res) == unicode
        assert res == u"ÿ"

    def test_to_unicode_bytearray(self):
        # Calling `compat.to_unicode` with a `bytearray` containing unicode
        res = to_unicode(bytearray(b"\xc3\xbf"))
        assert type(res) == unicode
        assert res == u"ÿ"

    def test_to_unicode_bytearray_double_decode(self):
        #  Calling `compat.to_unicode` with an already decoded `bytearray`
        # This represents the double-decode issue, which can cause a `UnicodeEncodeError`
        #   `bytearray('\xc3\xbf').decode('utf-8').decode('utf-8')`
        res = to_unicode(bytearray(b"\xc3\xbf").decode("utf-8"))
        assert type(res) == unicode
        assert res == u"ÿ"

    def test_to_unicode_non_string(self):
        #  Calling `compat.to_unicode` on non-string types
        assert to_unicode(1) == u"1"
        assert to_unicode(True) == u"True"
        assert to_unicode(None) == u"None"
        assert to_unicode(dict(key="value")) == u"{'key': 'value'}"

    def test_get_connection_response(self):
        """Ensure that buffering is in kwargs."""

        class MockConn(object):
            def getresponse(self, *args, **kwargs):
                if PY3:
                    assert "buffering" not in kwargs
                else:
                    assert "buffering" in kwargs

        mock = MockConn()
        get_connection_response(mock)


class TestPy2Py3Compat(object):
    """Common tests to ensure functions are both Python 2 and
    Python 3 compatible.
    """

    def test_reraise(self):
        # ensure the `raise` function is Python 2/3 compatible
        with pytest.raises(Exception) as ex:
            try:
                raise Exception("Ouch!")
            except Exception:
                # original exception we want to re-raise
                (typ, val, tb) = sys.exc_info()
                try:
                    # this exception doesn't allow a re-raise, and we need
                    # to use the previous one collected via `exc_info()`
                    raise Exception("Obfuscate!")
                except Exception:
                    pass
                # this call must be Python 2 and 3 compatible
                raise reraise(typ, val, tb)
        assert ex.value.args[0] == "Ouch!"


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


@pytest.mark.skipif(PY2, reason="This hypothesis test hangs occasionally on Python 2")
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
    assert type(maybe_stringify(obj)) is (obj is not None and six.text_type or type(None))
