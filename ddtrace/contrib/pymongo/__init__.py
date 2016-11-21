"""Instrument pymongo to report MongoDB queries.

The pymongo integration works by wrapping pymongo's MongoClient to trace
network calls. Pymongo 3.0 and greater are the currently supported versions.
The monkey patching will patch the clients, which you can then configure.
Patch your MongoClient instance to make it work.

    # to patch all mongoengine connections, do the following
    # before you import mongoengine connect.

    from ddtrace import patch, Pin
    import pymongo
    patch(pymongo=True)

    # At that point, pymongo is instrumented with the default settings
    client = pymongo.MongoClient()
    # Example of instrumented query
    db = client["test-db"]
    db.teams.find({"name": "Toronto Maple Leafs"})

    # To customize one client instrumentation
    client = pymongo.MongoClient()
    ddtrace.Pin(service='my-mongo', tracer=Tracer()).onto(client)
"""
from ..util import require_modules

required_modules = ['pymongo']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .client import trace_mongo_client
        from .patch import patch
        __all__ = ['trace_mongo_client', 'patch']
