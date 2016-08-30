"""
The pymongo integration works by wrapping pymongo's MongoClient to trace
network calls. Pymongo 3.0 and greater are the currently supported versions.
Basic usage::

    from pymongo import MongoClient
    from ddtrace import tracer
    from ddtrace.contrib.pymongo import trace_mongo_client

    client = trace_mongo_client(
        MongoClient(), tracer, "my-mongo-db")

    db = client["test-db"]
    db.teams.find({"name": "Toronto Maple Leafs"})
"""

from ..util import require_modules

required_modules = ['pymongo']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .trace import trace_mongo_client
        __all__ = ['trace_mongo_client']
