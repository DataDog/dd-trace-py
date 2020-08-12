"""
Patch the built-in ``httplib``/``http.client`` libraries to trace all HTTP calls.


Usage::

    # Patch all supported modules/functions
    from ddtrace import patch
    patch(httplib=True)

    # Python 2
    import httplib
    import urllib

    resp = urllib.urlopen('http://www.datadog.com/')

    # Python 3
    import http.client
    import urllib.request

    resp = urllib.request.urlopen('http://www.datadog.com/')

``httplib`` spans do not include a default service name. Before HTTP calls are
made, ensure a parent span has been started with a service name to be used for
spans generated from those calls::

    with tracer.trace('main', service='my-httplib-operation'):
        resp = urllib.request.urlopen('http://www.datadog.com/')

The library can be configured globally and per instance, using the Configuration API::

    from ddtrace import config

    # disable distributed tracing globally
    config.httplib['distributed_tracing'] = False

    # change the service name/distributed tracing only for this HTTP connection

    # Python 2
    connection = urllib.HTTPConnection('www.datadog.com')

    # Python 3
    connection = http.client.HTTPConnection('www.datadog.com')

    cfg = config.get_from(connection)
    cfg['distributed_tracing'] = False

:ref:`Headers tracing <http-headers-tracing>` is supported for this integration.
"""
from .patch import patch, unpatch
__all__ = ['patch', 'unpatch']
