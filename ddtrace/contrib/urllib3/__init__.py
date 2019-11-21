"""
The ``urllib3`` integration instruments tracing on http calls with optional 
support for distributed tracing across services the client communicates with.

The ``patch`` function must be called before importing and using the library. 
Note that instrumentation is turned off by default when using the ``patch_all``
command and you must specifically patch urllib3 to enable ttracing.
For example:

    from ddtrace import patch
    patch(urllib3=True)
    import urllib3

    http = urllib3.PoolManager()
    r = http.request('GET', 'https://example.com/')

    print(r.status)
    print(r.data)

The auto-instrumentation of requests can be further tuned by configuring 
urllib3 through the Configuration API:

    from ddtrace import config
    
    # disable distributed tracing
    config.urllib3['distributed_tracing'] = False
    
    # enable app analytics
    config.urllib3['analytics_enabled'] = True

"""
from ...utils.importlib import require_modules


required_modules = ['urllib3']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch, unpatch

        __all__ = [
            'patch',
            'unpatch',
        ]
