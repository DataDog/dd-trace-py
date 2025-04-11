"""Instrument pylibmc to report Memcached queries.

``import ddtrace.auto`` will automatically patch your pylibmc client to make it work.
::

    # Be sure to import pylibmc and not pylibmc.Client directly,
    # otherwise you won't have access to the patched version
    from ddtrace import patch
    from ddtrace.trace import Pin
    import pylibmc

    # If not patched yet, you can patch pylibmc specifically
    patch(pylibmc=True)

    # One client instrumented with default configuration
    client = pylibmc.Client(["localhost:11211"]
    client.set("key1", "value1")

    # Use a pin to specify metadata related to this client
    Pin.override(client, service="memcached-sessions")
"""

from ddtrace.contrib.internal.pylibmc.client import TracedClient


__all__ = ["TracedClient"]
