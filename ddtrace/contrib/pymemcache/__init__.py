"""Instrument pymemcache to report memcached queries.

``patch_all`` will automatically patch the pymemcache ``Client``::

    from ddtrace import Pin, patch

    # If not patched yet, patch pymemcache specifically
    patch(pymemcache=True)

    # Import reference to Client AFTER patching
    from pymemcache.client.base import Client

    # This will report a span with the default settings
    client = Client(('localhost', 11211))
    client.set("my-key", "my-val")

    # Use a pin to specify metadata related to this client
    Pin.override(client, service='my-memcached-service')

Pymemcache's ``HashClient`` will also be indirectly patched as it uses
``Client``s under the hood.
"""

from .patch import patch, unpatch

__all__ = [patch, unpatch]
