"""Instrument Consul to trace KV queries.

Only supports tracing for the synchronous client.

``import ddtrace.auto`` will automatically patch your Consul client to make it work.
::

    from ddtrace import patch
    import consul

    # If not patched yet, you can patch consul specifically
    patch(consul=True)

    # This will report a span with the default settings
    client = consul.Consul(host="127.0.0.1", port=8500)
    client.get("my-key")

Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.consul["service"]

   The service name reported by default for consul spans.

   This option can also be set with the ``DD_CONSUL_SERVICE`` environment
   variable.

   Default: ``"consul"``
"""
