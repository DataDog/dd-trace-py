"""Instrument Elasticsearch to report Elasticsearch queries.

Patch your Elasticsearch instance to make it work.

    from ddtrace import Pin, patch
    import elasticsearch

    # Instrument Elasticsearch
    patch(elasticsearch=True)

    # This will report spans with the default instrumentation
    es = elasticsearch.Elasticsearch(port=ELASTICSEARCH_CONFIG['port'])
    # Example of instrumented query
    es.indices.create(index='index-one', ignore=400)

    # Customize one client instrumentation
    es = elasticsearch.Elasticsearch(port=ELASTICSEARCH_CONFIG['port'])
    Pin(service='es-two').onto(es)
"""
from ..util import require_modules

required_modules = ['elasticsearch']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .transport import get_traced_transport
        from .patch import patch

        __all__ = ['get_traced_transport', 'patch']
