"""
The OpenSearch integration will trace OpenSearch queries.

Enabling
~~~~~~~~

The opensearch integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :func:`patch_all()<ddtrace.patch_all>`.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    from opensearchpy import OpenSearch

    patch(opensearch=True)
    # This will report spans with the default instrumentation
    os = OpenSearch(port=OPENSEARCH_CONFIG['port'])
    # Example of instrumented query
    os.indices.create(index='books', ignore=400)

    # Use a pin to specify metadata related to this client
    os = OpenSearch(port=OPENSEARCH_CONFIG['port'])
    Pin.override(os.transport, service='opensearch-videos')
    os.indices.create(index='videos', ignore=400)



Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.opensearch['service']

   The service name reported for your OpenSearch app.


Example::

    from ddtrace import config

    # Override service name
    config.opensearch['service'] = 'custom-service-name'
"""
from ...internal.utils.importlib import require_modules


required_modules = ["opensearchpy"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch
        from .patch import unpatch

        __all__ = [
            "patch",
            "unpatch",
        ]
