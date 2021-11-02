"""
The Elasticsearch integration will trace Elasticsearch queries.

Enabling
~~~~~~~~

The elasticsearch integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`patch_all()<patch_all>`.

Or use :ref:`patch()<patch>` to manually enable the integration::

    from ddtrace import patch
    from elasticsearch import Elasticsearch

    patch(elasticsearch=True)
    # This will report spans with the default instrumentation
    es = Elasticsearch(port=ELASTICSEARCH_CONFIG['port'])
    # Example of instrumented query
    es.indices.create(index='books', ignore=400)




Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.elasticsearch['service_name']

   The service name reported for your elasticsearch app.


Example::

    from ddtrace import config

    # Override service name
    config.elasticsearch['service_name'] = 'custom-service-name'
"""
from .patch import patch


__all__ = ["patch"]
