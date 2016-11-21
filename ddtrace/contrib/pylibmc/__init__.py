"""Instrument pylibmc to report Memcached queries.

Patch your pylibmc client to make it work.

    # Be sure to import pylibmc and not Client directly,
    # otherwise you won't have access to the patched version
    import pylibmc
    import ddtrace
    from ddtrace import patch, Pin

    # patch the library
    patch(pylibmc=True)

    # One client instrumented with default configuration
    client = pylibmc.Client(["localhost:11211"]
    client.set("key1", "value1")

    # Configure one client instrumentation
    ddtrace.Pin(service='my-cache-cluster')).onto(client)
"""

from ..util import require_modules

required_modules = ['pylibmc']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .client import TracedClient
        from .patch import patch

        __all__ = ['TracedClient', 'patch']
