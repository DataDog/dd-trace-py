"""
A patched pylibmc Memcached client will wrap report spans for any Memcached call.

Basic usage::

    import pylibmc
    import ddtrace
    from ddtrace.monkey import patch_all

    # patch the library
    patch_all()

    # one client with default configuration
    client = pylibmc.Client(["localhost:11211"]
    client.set("key1", "value1")

    # Configure one client
    ddtrace.Pin(service='my-cache-cluster')).onto(client)

"""

from ..util import require_modules

required_modules = ['pylibmc']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .client import TracedClient
        from .patch import patch

        __all__ = ['TracedClient', 'patch']
