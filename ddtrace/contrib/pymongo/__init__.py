"""Instrument pymongo to report MongoDB queries.

The pymongo integration works by wrapping pymongo's MongoClient to trace
network calls. Pymongo 3.0 and greater are the currently supported versions.
``patch_all`` will automatically patch your MongoClient instance to make it work.

::

    # Be sure to import pymongo and not pymongo.MongoClient directly,
    # otherwise you won't have access to the patched version
    from ddtrace import Pin, patch
    import pymongo

    # If not patched yet, you can patch pymongo specifically
    patch(pymongo=True)

    # At that point, pymongo is instrumented with the default settings
    client = pymongo.MongoClient()
    # Example of instrumented query
    db = client["test-db"]
    db.teams.find({"name": "Toronto Maple Leafs"})

    # Use a pin to specify metadata related to this client
    client = pymongo.MongoClient()
    pin = Pin.override(client, service="mongo-master")

Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.pymongo["service"]
   The service name reported by default for pymongo spans

   The option can also be set with the ``DD_PYMONGO_SERVICE`` environment variable

   Default: ``"pymongo"``

"""
from ...internal.utils.importlib import require_modules


required_modules = ["pymongo"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import get_version
        from .patch import patch

        __all__ = ["patch", "get_version"]
