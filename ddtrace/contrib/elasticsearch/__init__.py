"""Instrument Elasticsearch to report Elasticsearch queries.

``patch_all`` will automatically patch your Elasticsearch instance to make it work.
::

    from ddtrace import Pin, patch
    from elasticsearch import Elasticsearch

    # If not patched yet, you can patch elasticsearch specifically
    patch(elasticsearch=True)

    # This will report spans with the default instrumentation
    es = Elasticsearch(port=ELASTICSEARCH_CONFIG['port'])
    # Example of instrumented query
    es.indices.create(index='books', ignore=400)

    # Use a pin to specify metadata related to this client
    es = Elasticsearch(port=ELASTICSEARCH_CONFIG['port'])
    Pin.override(es.transport, service='elasticsearch-videos')
    es.indices.create(index='videos', ignore=400)
"""
from .patch import patch


__all__ = ["patch"]
