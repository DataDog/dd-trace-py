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

try:
    import simplejson as json
except ImportError:
    import json

def iteritems(obj, **kwargs):
    func = getattr(obj, "iteritems", None)
    if not func:
        func = obj.items
    return func(**kwargs)

def to_unicode(s):
    """ Return a unicode string for the given bytes or string instance. """
    if hasattr(s, "decode"):
        return s.decode("utf-8")
    else:
        return stringify(s)

if PY2:
    numeric_types = (int, long, float)
else:
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
