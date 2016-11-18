import sys

PY2 = sys.version_info[0] == 2

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
    import urlparse
except ImportError:
    from urllib import parse as urlparse

# TODO[manu]: forcing built-in JSON for benchmark reasons
import json

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


if PY2:
    string_type = basestring
    numeric_types = (int, long, float)
else:
    string_type = str
    numeric_types = (int, float)


__all__ = [
    'httplib',
    'iteritems',
    'json',
    'PY2',
    'Queue',
    'stringify',
    'StringIO',
    'urlencode',
    'urlparse',
]
