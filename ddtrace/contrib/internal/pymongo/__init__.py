"""Instrument pymongo to report MongoDB queries.

The pymongo integration works by wrapping pymongo's MongoClient and AsyncMongoClient
to trace network calls. Pymongo 3.0+ is supported for synchronous operations.
AsyncMongoClient support requires pymongo 4.12+. ``import ddtrace.auto`` will
automatically patch both client types.

::

    from ddtrace import patch
    import pymongo

    patch(pymongo=True)

    # Synchronous usage
    client = pymongo.MongoClient()
    db = client["test-db"]
    db.teams.find({"name": "Toronto Maple Leafs"})

    # Asynchronous usage (pymongo 4.12+)
    from pymongo.asynchronous.mongo_client import AsyncMongoClient

    async def example():
        client = AsyncMongoClient()
        db = client["test-db"]
        async for doc in db.teams.find({"name": "Toronto Maple Leafs"}):
            print(doc)
        await client.close()

Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.pymongo["service"]
   The service name reported by default for pymongo spans

   The option can also be set with the ``DD_PYMONGO_SERVICE`` environment variable

   Default: ``"pymongo"``

"""
