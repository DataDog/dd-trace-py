"""Instrument pymemcache to report memcached queries.

``patch_all`` will automatically patch the pymemcache client::

    from ddtrace import Pin, patch
    import pymemcache

    # If not patched yet, patch pymemcache specifically
    patch(pymemcache=True)

    # This will report a span with the default settings
    client = pymemcache.client.base.Client(('localhost', 11211))
    client.set("my-key", "my-val")

    # Use a pin to specify metadata related to this client
    Pin.override(client, service='my-memcached-service')
"""

from .patch import patch, unpatch

__all__ = [
    patch,
    unpatch,
]
