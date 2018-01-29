"""
To trace all HTTP calls from the requests library, patch the library like so::

    # Patch the requests library.
    from ddtrace.contrib.requests import patch
    patch()

    import requests
    requests.get("http://www.datadog.com")

If you would prefer finer grained control without monkeypatching the requests'
code, use a TracedSession object as you would a requests.Session::

    from ddtrace.contrib.requests import TracedSession

    session = TracedSession()
    session.get("http://www.datadog.com")

To enable distributed tracing, for example if you call, from requests, a web service
which is also instrumented and want to have traces including both client and server sides::

    from ddtrace.contrib.requests import TracedSession

    session = TracedSession()
    session.distributed_tracing = True
    session.get("http://host.lan/webservice")
"""


from ..util import require_modules

required_modules = ['requests']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import TracedSession, patch, unpatch
        __all__ = ['TracedSession', 'patch', 'unpatch']
