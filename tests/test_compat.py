# -*- coding: utf-8 -*-
import os
import sys

import pytest

from ddtrace import compat
from ddtrace.compat import to_unicode, PY3, reraise, get_connection_response, is_integer


if PY3:
    unicode = str


class TestCompat(object):

    def test_to_unicode_string(self):
        # Calling `compat.to_unicode` on a non-unicode string
        res = to_unicode(b'test')
        assert type(res) == unicode
        assert res == 'test'

    def test_to_unicode_unicode_encoded(self):
        # Calling `compat.to_unicode` on a unicode encoded string
        res = to_unicode(b'\xc3\xbf')
        assert type(res) == unicode
        assert res == u'ÿ'

    def test_to_unicode_unicode_double_decode(self):
        # Calling `compat.to_unicode` on a unicode decoded string
        # This represents the double-decode issue, which can cause a `UnicodeEncodeError`
        #   `'\xc3\xbf'.decode('utf-8').decode('utf-8')`
        res = to_unicode(b'\xc3\xbf'.decode('utf-8'))
        assert type(res) == unicode
        assert res == u'ÿ'

    def test_to_unicode_unicode_string(self):
        # Calling `compat.to_unicode` on a unicode string
        res = to_unicode(u'ÿ')
        assert type(res) == unicode
        assert res == u'ÿ'

    def test_to_unicode_bytearray(self):
        # Calling `compat.to_unicode` with a `bytearray` containing unicode
        res = to_unicode(bytearray(b'\xc3\xbf'))
        assert type(res) == unicode
        assert res == u'ÿ'

    def test_to_unicode_bytearray_double_decode(self):
        #  Calling `compat.to_unicode` with an already decoded `bytearray`
        # This represents the double-decode issue, which can cause a `UnicodeEncodeError`
        #   `bytearray('\xc3\xbf').decode('utf-8').decode('utf-8')`
        res = to_unicode(bytearray(b'\xc3\xbf').decode('utf-8'))
        assert type(res) == unicode
        assert res == u'ÿ'

    def test_to_unicode_non_string(self):
        #  Calling `compat.to_unicode` on non-string types
        assert to_unicode(1) == u'1'
        assert to_unicode(True) == u'True'
        assert to_unicode(None) == u'None'
        assert to_unicode(dict(key='value')) == u'{\'key\': \'value\'}'

    def test_get_connection_response(self):
        """Ensure that buffering is in kwargs."""

        class MockConn(object):
            def getresponse(self, *args, **kwargs):
                if PY3:
                    assert 'buffering' not in kwargs
                else:
                    assert 'buffering' in kwargs

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
                raise Exception('Ouch!')
            except Exception:
                # original exception we want to re-raise
                (typ, val, tb) = sys.exc_info()
                try:
                    # this exception doesn't allow a re-raise, and we need
                    # to use the previous one collected via `exc_info()`
                    raise Exception('Obfuscate!')
                except Exception:
                    pass
                # this call must be Python 2 and 3 compatible
                raise reraise(typ, val, tb)
        assert ex.value.args[0] == 'Ouch!'


@pytest.mark.parametrize('obj,expected', [
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
])
def test_is_integer(obj, expected):
    assert is_integer(obj) is expected


@pytest.mark.skipif(not getattr(compat, "register_at_fork"), reason="register_at_fork not supported on this platform")
def test_register_at_fork_before():
    check = {"called": 0}

    def before1():
        check["called"] += 10

    def before2():
        check["called"] /= 2

    compat.register_at_fork(before=before2)
    compat.register_at_fork(before=before1)

    child = os.fork()

    if child == 0:
        assert check['called'] == 5
        os._exit(42)

    pid, status = os.waitpid(child, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 42

    assert check['called'] == 5


@pytest.mark.skipif(not getattr(compat, "register_at_fork"), reason="register_at_fork not supported on this platform")
def test_register_at_fork_before_error():
    check = {"called": 0}

    def before():
        check["called"] += 1
        raise RuntimeError

    compat.register_at_fork(before=before)

    child = os.fork()

    if child == 0:
        assert check['called'] == 1
        os._exit(42)

    pid, status = os.waitpid(child, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 42

    assert check['called'] == 1


@pytest.mark.skipif(not getattr(compat, "register_at_fork"), reason="register_at_fork not supported on this platform")
def test_register_at_fork_after_in_child():
    check = {"called": 0}

    def after1():
        check["called"] += 10

    def after2():
        check["called"] /= 2

    compat.register_at_fork(after_in_child=after1)
    compat.register_at_fork(after_in_child=after2)

    child = os.fork()

    if child == 0:
        assert check['called'] == 5
        os._exit(42)

    pid, status = os.waitpid(child, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 42

    assert check['called'] == 0


@pytest.mark.skipif(not getattr(compat, "register_at_fork"), reason="register_at_fork not supported on this platform")
def test_register_at_fork_after_in_parent():
    check = {"called": 0}

    def after1():
        check["called"] += 10

    def after2():
        check["called"] /= 2

    compat.register_at_fork(after_in_parent=after1)
    compat.register_at_fork(after_in_parent=after2)

    child = os.fork()

    if child == 0:
        assert check['called'] == 0
        os._exit(42)

    pid, status = os.waitpid(child, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 42

    assert check['called'] == 5
