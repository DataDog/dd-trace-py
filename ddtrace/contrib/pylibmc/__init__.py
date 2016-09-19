"""
To trace the pylibmc Memcached client, wrap its connections with the traced
client::

    import pylibmc
    from ddtrace import tracer

    client = TracedClient(
        client=pylibmc.Client(["localhost:11211"]),
        tracer=tracer,
        service="my-cache-cluster")

    client.set("key", "value")
"""

from .client import TracedClient # flake8: noqa
