"""
The pymongo integration works by wrapping pymongo's MongoClient to trace
network calls. Pymongo 3.0 and greater are the currently supported versions.
The monkey patching will patch the clients, which you can then configure.
Basic usage::

    import pymongo
    import ddtrace
    from ddtrace.monkey import patch_all

    # First, patch libraries
    patch_all()

    # MongoClient with default configuration
    client = pymongo.MongoClient()

    # Configure one client
    ddtrace.Pin(service='my-mongo', tracer=Tracer()).onto(client)

    # From there, queries are traced
    db = client["test-db"]
    db.teams.find({"name": "Toronto Maple Leafs"})  # This we generate a span
"""

from ..util import require_modules

required_modules = ['pymongo']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .client import trace_mongo_client
        from .patch import patch
        __all__ = ['trace_mongo_client', 'patch']
