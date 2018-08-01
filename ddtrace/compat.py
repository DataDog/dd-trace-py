import sys
import platform

PYTHON_VERSION_INFO = sys.version_info
PY2 = sys.version_info[0] == 2

# Infos about python passed to the trace agent through the header
PYTHON_VERSION = platform.python_version()
PYTHON_INTERPRETER = platform.python_implementation()

stringify = str

if PY2:
    from urllib import urlencode
    import httplib
    stringify = unicode
    from Queue import Queue
    try:
        from cStringIO import StringIO
    except ImportError:
        from StringIO import StringIO
else:
    from queue import Queue
    from urllib.parse import urlencode
    import http.client as httplib
    from io import StringIO

try:
    import urlparse as parse
except ImportError:
    from urllib import parse

try:
    from asyncio import iscoroutinefunction
    from .compat_async import _make_async_decorator as make_async_decorator
except ImportError:
    # asyncio is missing so we can't have coroutines; these
    # functions are used only to ensure code executions in case
    # of an unexpected behavior
    def iscoroutinefunction(fn):
        return False

    def make_async_decorator(tracer, fn, *params, **kw_params):
        return fn


def iteritems(obj, **kwargs):
    func = getattr(obj, "iteritems", None)
    if not func:
        func = obj.items
    return func(**kwargs)


def to_unicode(s):
    """ Return a unicode string for the given bytes or string instance. """
    # No reason to decode if we already have the unicode compatible object we expect
    # DEV: `stringify` will be a `str` for python 3 and `unicode` for python 2
    # DEV: Double decoding a `unicode` can cause a `UnicodeEncodeError`
    #   e.g. `'\xc3\xbf'.decode('utf-8').decode('utf-8')`
    if isinstance(s, stringify):
        return s

    # If the object has a `decode` method, then decode into `utf-8`
    #   e.g. Python 2 `str`, Python 2/3 `bytearray`, etc
    if hasattr(s, 'decode'):
        return s.decode('utf-8')

    # Always try to coerce the object into the `stringify` object we expect
    #   e.g. `to_unicode(1)`, `to_unicode(dict(key='value'))`
    return stringify(s)


def get_connection_response(conn):
    """Returns the response for a connection.

    If using Python 2 enable buffering.

    Python 2 does not enable buffering by default resulting in many recv
    syscalls.

    See:
    https://bugs.python.org/issue4879
    https://github.com/python/cpython/commit/3c43fcba8b67ea0cec4a443c755ce5f25990a6cf
    """
    if PY2:
        return conn.getresponse(buffering=True)
    else:
        return conn.getresponse()


if PY2:
    string_type = basestring
    msgpack_type = basestring
    numeric_types = (int, long, float)
else:
    string_type = str
    msgpack_type = bytes
    numeric_types = (int, float)

if PY2:
    # avoids Python 3 `SyntaxError`
    # this block will be replaced with the `six` library
    from .utils.reraise import _reraise as reraise
else:
    def reraise(tp, value, tb=None):
        """Python 3 re-raise function. This function is internal and
        will be replaced entirely with the `six` library.
        """
        try:
            if value is None:
                value = tp()
            if value.__traceback__ is not tb:
                raise value.with_traceback(tb)
            raise value
        finally:
            value = None
            tb = None


__all__ = [
    'httplib',
    'iteritems',
    'PY2',
    'Queue',
    'stringify',
    'StringIO',
    'urlencode',
    'parse',
    'reraise',
]
