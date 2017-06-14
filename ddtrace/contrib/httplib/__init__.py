"""
Patch the built-in httplib/http.client libraries to trace all HTTP calls.


Usage::

    # Patch all supported modules/functions
    from ddtrace import patch
    patch(httplib=True)

    # Python 2
    from ddtrace import Pin
    import httplib
    import urllib

    # Use a Pin to specify metadata for all http requests
    Pin.override(httplib, service='httplib')
    resp = urllib.urlopen('http://www.datadog.com/')

    # Python 3
    from ddtrace import Pin
    import http.client
    import urllib.request

    # Use a Pin to specify metadata for all http requests
    Pin.override(http.client, service='httplib')
    resp = urllib.request.urlopen('http://www.datadog.com/')

"""
from .patch import patch, unpatch
__all__ = ['patch', 'unpatch']
