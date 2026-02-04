"""Instrument pymemcache to report memcached queries.

``import ddtrace.auto`` will automatically patch the pymemcache ``Client``::

    from ddtrace import patch

    # If not patched yet, patch pymemcache specifically
    patch(pymemcache=True)

    # Import reference to Client AFTER patching
    import pymemcache
    from pymemcache.client.base import Client

    # This will report a span with the default settings
    client = Client(('localhost', 11211))
    client.set("my-key", "my-val")

Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.pymemcache["service"]

   The service name reported by default for pymemcache spans.

   This option can also be set with the ``DD_PYMEMCACHE_SERVICE`` environment
   variable.

   Default: ``"pymemcache"``

Pymemcache ``HashClient`` will also be indirectly patched as it uses ``Client``
under the hood.
"""
