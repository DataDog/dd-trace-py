"""Instrument Consul to trace KV queries.

Only supports tracing for the synchronous client.

``import ddtrace.auto`` will automatically patch your Consul client to make it work.
::

    from ddtrace import patch
    from ddtrace.trace import Pin
    import consul

    # If not patched yet, you can patch consul specifically
    patch(consul=True)

    # This will report a span with the default settings
    client = consul.Consul(host="127.0.0.1", port=8500)
    client.get("my-key")

    # Use a pin to specify metadata related to this client
    Pin.override(client, service='consul-kv')
"""


# Required to allow users to import from  `ddtrace.contrib.consul.patch` directly
import warnings as _w


with _w.catch_warnings():
    _w.simplefilter("ignore", DeprecationWarning)
    from . import patch as _  # noqa: F401, I001


from ddtrace.contrib.internal.consul.patch import get_version  # noqa: F401
from ddtrace.contrib.internal.consul.patch import patch  # noqa: F401
from ddtrace.contrib.internal.consul.patch import unpatch  # noqa: F401
